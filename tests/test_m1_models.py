"""NB2 prior model, maturity curve fit (replicated), regimes and planned-post guardrails."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.core.types import ForecastSubject, PriorContext
from socialmgr.db import migrate
from socialmgr.db.models import Regime
from socialmgr.db.session import make_engine, session_factory
from socialmgr.modelling import maturity
from socialmgr.modelling.models.nb2 import NB2PriorModel
from socialmgr.modelling.priors.hand_set import HandSetPrior
from socialmgr.modelling.regimes import FileVersion, github_fetcher
from socialmgr.services import planned, regimes
from socialmgr.services.fixtures import load_fixture
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings

T = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _median(**kw: object) -> float:
    prior = HandSetPrior().prior(PriorContext(platform="x", followers=0, owner_matured_posts=0))
    fitted = NB2PriorModel().fit([], prior, seed=1)
    base = {"subject_id": "post:1", "created_at": T, "type": "original", "followers_at_post": 500}
    subject = ForecastSubject.model_validate(base | kw)
    return float(np.median(fitted.predict(subject).samples))


def test_nb2_prior_predictive_moves_the_right_way() -> None:
    assert _median(followers_at_post=5000) > _median(followers_at_post=500) > _median(followers_at_post=0)
    assert _median(type="reply") < _median(type="original")
    assert _median(has_url=True) < _median(has_url=False)
    assert 50 < _median(followers_at_post=0) < 300  # around the placeholder exp(log 100)


def test_nb2_draws_are_reproducible_and_flagged_prior_only() -> None:
    prior = HandSetPrior().prior(PriorContext(platform="x", followers=0, owner_matured_posts=0))
    subject = ForecastSubject(subject_id="post:7", created_at=T, type="original", followers_at_post=100)
    a = NB2PriorModel().fit([], prior, seed=3).predict(subject)
    b = NB2PriorModel().fit([], prior, seed=3).predict(subject)
    assert np.array_equal(a.samples, b.samples) and a.samples.shape == (1000,)
    assert a.meta["uses_owner_data"] == "false"
    assert NB2PriorModel().fit([], prior, seed=3).version == "nb2@prior-0.1+hand_set@0.2"


def _simulate_pairs(
    true: maturity.Curve, rng: np.random.Generator, n_posts: int
) -> list[maturity.CapturePair]:
    pairs: list[maturity.CapturePair] = []
    for _ in range(n_posts):
        i48 = rng.lognormal(6, 1)
        ages = np.sort(rng.uniform(0.5, 200, 3))[::-1]
        captures, prev = [], None
        for a in ages:  # nested: each earlier count is a binomial thinning of the later one
            y = (
                rng.poisson(i48 * true.g(a))
                if prev is None
                else rng.binomial(prev[1], true.g(a) / true.g(prev[0]))
            )
            captures.append((float(a), int(y)))
            prev = (a, y)
        pairs += maturity.pairs_from_captures(captures)
    return pairs


@pytest.mark.parametrize(
    "true", [maturity.Curve("a", 6.0, 0.6), maturity.Curve("b", 1.5, 0.35), maturity.PRIOR]
)
def test_curve_fit_recovers_known_curves_over_replicates(true: maturity.Curve) -> None:
    errors = []
    for rep in range(20):
        fitted = maturity.fit(_simulate_pairs(true, np.random.default_rng(rep), 150))
        errors.append(max(abs(fitted.g(a) / true.g(a) - 1) for a in (1.0, 6.0, 24.0, 168.0)))
    assert np.median(errors) < 0.03 and max(errors) < 0.08


def test_curve_fit_falls_back_to_the_prior_with_too_few_pairs() -> None:
    pairs = _simulate_pairs(maturity.Curve("a", 6.0, 0.6), np.random.default_rng(0), 5)
    assert maturity.fit(pairs) is maturity.PRIOR


def _db(tmp_path: Path) -> tuple[Settings, Session]:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return settings, session_factory(engine)()


def test_regimes_seed_once_then_open_on_param_rs_change(tmp_path: Path) -> None:
    settings, session = _db(tmp_path)
    versions = iter(
        [
            FileVersion("aaa", T - timedelta(days=3), "sync"),
            FileVersion("aaa", T - timedelta(days=3), "sync"),
            FileVersion("bbb", T + timedelta(hours=1), "Raise OON weight"),
        ]
    )
    first = regimes.check_regimes(session, settings, lambda: next(versions), T)
    assert first.seeded == 13 and first.opened == []  # baseline recorded, nothing opened
    assert regimes.check_regimes(session, settings, lambda: next(versions), T).seeded == 0
    third = regimes.check_regimes(session, settings, lambda: next(versions), T + timedelta(days=1))
    assert third.opened == ["bbb"]
    opened = session.scalar(select(Regime).where(Regime.source == "param_rs"))
    assert opened is not None and opened.description == "param.rs changed: Raise OON weight"


def test_regime_fetch_failure_is_a_caveat(tmp_path: Path) -> None:
    settings, session = _db(tmp_path)

    def boom() -> FileVersion:
        raise httpx.ConnectError("offline")

    assert "offline" in regimes.check_regimes(session, settings, boom, T).caveats[0]


def test_github_fetcher_parses_the_latest_commit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["path"] == "home-mixer/params/param.rs"
        commit = {
            "sha": "abc123",
            "commit": {"committer": {"date": "2026-11-18T10:00:00Z"}, "message": "Sync\n\nx"},
        }
        return httpx.Response(200, json=[commit])

    version = github_fetcher(httpx.Client(transport=httpx.MockTransport(handler)))()
    assert version == FileVersion("abc123", datetime(2026, 11, 18, 10, tzinfo=UTC), "Sync")


def test_planned_post_guardrails(tmp_path: Path) -> None:
    settings, session = _db(tmp_path)
    plugins = load_plugins(settings)
    load_fixture(session, settings, plugins, until=datetime(2026, 10, 20, 6, tzinfo=UTC))
    now = datetime(2026, 10, 20, 8, tzinfo=UTC)
    existing = session.execute(
        select(planned.Post.text).where(planned.Post.text.is_not(None)).limit(1)
    ).scalar_one()

    with pytest.raises(ValueError, match="future"):
        planned.register_planned_post(
            session, settings, plugins, planned.PlanRequest(planned_at=now), now=now
        )
    with pytest.raises(planned.PlanRefused, match="Near-duplicate"):
        req = planned.PlanRequest(planned_at=now + timedelta(days=2), text=existing)
        planned.register_planned_post(session, settings, plugins, req, now=now)

    first = planned.register_planned_post(
        session, settings, plugins, planned.PlanRequest(planned_at=now + timedelta(days=3)), now=now
    )
    assert first.guardrails == []
    close = planned.PlanRequest(planned_at=now + timedelta(days=3, hours=1))
    with pytest.raises(planned.PlanRefused, match="Within 3h"):
        planned.register_planned_post(session, settings, plugins, close, now=now, allow_warnings=False)
    warned = planned.register_planned_post(session, settings, plugins, close, now=now)
    assert [h.rule for h in warned.guardrails] == ["original_spacing"]
    assert warned.forecast.frozen_at == now and warned.forecast.subject == f"slot:{warned.slot.id}"
