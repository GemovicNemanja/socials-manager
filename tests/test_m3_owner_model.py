"""Owner-level NB2 (MODEL §2a, plan §6): prior-only below 20 posts, bit-for-bit fits, sampler
diagnostics, recovery of known owner parameters (replicated) and SBC (slow)."""

import math
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy import integrate, special, stats

from socialmgr import plugins
from socialmgr.core.types import ForecastSubject, PriorContext, TrainingExample
from socialmgr.modelling import mcmc
from socialmgr.modelling.models import nb2_owner
from socialmgr.modelling.models.nb2 import FittedNB2, NB2PriorModel
from socialmgr.modelling.models.nb2_owner import FittedOwnerNB2, NB2OwnerModel, log_lik_points
from socialmgr.modelling.priors.hand_set import HandSetPrior
from socialmgr.modelling.sbc import SBC_RESULTS, run_sbc
from socialmgr.services.pipeline import forecast_from_manifest
from socialmgr.services.plugins import ActivePlugins, load_plugins
from tests.conftest import make_settings
from tests.owner_sim import Truth, simulate

PRIOR = HandSetPrior().prior(PriorContext(platform="x", followers=0, owner_matured_posts=0))
T = datetime(2026, 6, 1, 12, tzinfo=UTC)
SUBJECTS = [
    ForecastSubject(subject_id="post:91", created_at=T, type="original", followers_at_post=900),
    ForecastSubject(subject_id="post:92", created_at=T, type="reply", followers_at_post=900),
    ForecastSubject(subject_id="slot:3", created_at=T, type="quote", followers_at_post=900, has_url=True),
]


def _history(n: int, seed: int = 0, truth: Truth | None = None) -> list[TrainingExample]:
    return [p.example for p in simulate(truth or Truth(), n, np.random.default_rng(seed))]


def _peer(i: int) -> TrainingExample:
    return TrainingExample(
        post_external_id=f"peer{i}",
        account_role="peer",
        created_at=T - timedelta(days=i + 1),
        type="original",
        followers_at_post=5000,
        outcome_i48=1234.0,
    )


def test_below_twenty_owner_posts_is_exactly_the_prior_only_model() -> None:
    """Same draws and version string as today's champion, so frozen forecasts still recompute."""
    history = _history(19) + [_peer(i) for i in range(30)]  # peers don't count toward the 20
    owner = NB2OwnerModel().fit(history, PRIOR, seed=11)
    prior_only = NB2PriorModel().fit(history, PRIOR, seed=11)
    assert isinstance(owner, FittedNB2)
    assert owner.version == prior_only.version == "nb2@prior-0.1+hand_set@0.2"
    for subject in SUBJECTS:
        a, b = owner.predict(subject), prior_only.predict(subject)
        assert np.array_equal(a.samples, b.samples)
        assert a.meta == b.meta and a.meta["uses_owner_data"] == "false"


def test_owner_fit_is_bit_for_bit_and_cached() -> None:
    history = _history(25, seed=3)
    nb2_owner.clear_cache()
    first = NB2OwnerModel().fit(history, PRIOR, seed=7)
    assert nb2_owner.cache_size() == 1
    assert NB2OwnerModel().fit(history, PRIOR, seed=7) is first  # cache hit: same training, prior, seed
    nb2_owner.clear_cache()
    again = NB2OwnerModel().fit(history, PRIOR, seed=7)
    assert again is not first
    assert isinstance(first, FittedOwnerNB2) and isinstance(again, FittedOwnerNB2)
    for k, v in first.draws.items():
        assert np.array_equal(v, again.draws[k]), k
    for subject in SUBJECTS:
        assert np.array_equal(first.predict(subject).samples, again.predict(subject).samples)
    other_seed = NB2OwnerModel().fit(history, PRIOR, seed=8)
    assert not np.array_equal(other_seed.predict(SUBJECTS[0]).samples, first.predict(SUBJECTS[0]).samples)
    one_more = NB2OwnerModel().fit([*history, _history(26, seed=3)[-1]], PRIOR, seed=7)
    assert one_more is not first and nb2_owner.cache_size() == 3


def test_owner_variant_version_meta_and_diagnostics() -> None:
    fitted = NB2OwnerModel().fit(_history(40, seed=5), PRIOR, seed=1)
    assert isinstance(fitted, FittedOwnerNB2)
    assert fitted.version == "nb2@owner-0.1+hand_set@0.2"
    draws = fitted.predict(SUBJECTS[0])
    assert draws.samples.shape == (1000,)
    assert draws.meta["uses_owner_data"] == "true"
    assert draws.meta["owner_posts_seen"] == "40"
    assert draws.meta["model_variant"] == "owner"
    assert float(draws.meta["rhat_max"]) < 1.01 and float(draws.meta["ess_bulk_min"]) > 400
    d = fitted.diagnostics
    assert set(d.rhat) == {"level", "a_in", "a_oon", "beta", "log_phi", "lambda", "rho_th"}
    assert d.chains == 4 and d.draws_per_chain == 1500
    # predictive moves the right way
    reply, original = fitted.predict(SUBJECTS[1]), draws
    assert np.median(reply.samples) < np.median(original.samples)


def test_registry_keeps_nb2_and_adds_nb2_owner(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert isinstance(plugins.models.resolve("nb2", settings), NB2PriorModel)
    assert isinstance(plugins.models.resolve("nb2_owner", settings), NB2OwnerModel)


def _manifest(training: list[TrainingExample], seed: int) -> dict[str, Any]:
    return {
        "subject": {
            "subject_id": "post:500",
            "created_at": T.isoformat(),
            "type": "original",
            "followers_at_post": 1200,
            "has_url": False,
            "format": None,
            "topic": None,
        },
        "training": [e.model_dump(mode="json") for e in training],
        "seed": seed,
    }


def test_forecast_from_manifest_recomputes_with_the_owner_champion(tmp_path: Path) -> None:
    """Integration through the pipeline's recompute path with ``champion_model = "nb2_owner"``."""
    base = load_plugins(make_settings(tmp_path))
    active = ActivePlugins(
        providers=base.providers,
        prior_source=base.prior_source,
        champion=NB2OwnerModel(),
        challengers=base.challengers,
        labeler=base.labeler,
    )
    manifest = _manifest(_history(30, seed=9), seed=42)
    nb2_owner.clear_cache()
    v1, calibrated, d1 = forecast_from_manifest(active, manifest)
    nb2_owner.clear_cache()
    v2, _, d2 = forecast_from_manifest(active, manifest)
    assert v1 == v2 == "nb2@owner-0.1+hand_set@0.2" and calibrated
    assert np.array_equal(d1.samples, d2.samples)
    # Under 20 owner posts: today's prior-only forecast, labelled uncalibrated.
    small = _manifest(_history(12, seed=9), seed=42)
    v3, calibrated3, d3 = forecast_from_manifest(active, small)
    prior_active = ActivePlugins(
        providers=base.providers,
        prior_source=base.prior_source,
        champion=NB2PriorModel(),
        challengers=base.challengers,
        labeler=base.labeler,
    )
    v4, _, d4 = forecast_from_manifest(prior_active, small)
    assert v3 == v4 and not calibrated3 and np.array_equal(d3.samples, d4.samples)


def _brute_loglik(y: int, m: float, lam: float, phi: float) -> float:
    """log of the integral of NB2(y | e^(m + lam q), phi) N(q; 0, 1) dq, plus log Gamma(y + 1)."""

    def logf(q: float) -> float:
        mu = math.exp(m + lam * q)
        return float(stats.nbinom.logpmf(y, phi, phi / (phi + mu))) + float(stats.norm.logpdf(q))

    peak = min(max((math.log(y + 0.5) - m) / lam, -8.0), 8.0)
    shift = max(logf(peak), logf(0.0))
    value, _ = integrate.quad(lambda q: math.exp(logf(q) - shift), -9, 9, limit=500, points=[peak])
    return math.log(value) + shift + float(special.gammaln(y + 1))


def test_adaptive_gauss_hermite_matches_numerical_integration() -> None:
    """Within 3 predictive sds of the outcome, the 9-node quadrature is accurate to 0.01 nats."""
    worst = 0.0
    for y in (0, 3, 40, 500, 20000):
        for lam in (0.05, 0.5, 1.5):
            for phi in (0.5, 2.0, 20.0, 200.0):
                sd = math.sqrt(lam * lam + 1 / phi + 1 / (y + 0.5))
                for offset in (-2.5, 0.0, 2.5):
                    m = math.log(y + 0.5) + offset * sd
                    got = log_lik_points(
                        np.array([[m]]), np.array([lam]), np.array([math.log(phi)]), np.array([float(y)])
                    )[0, 0]
                    worst = max(worst, abs(got - _brute_loglik(y, m, lam, phi)))
    assert worst < 0.01, worst


def test_rhat_and_ess_on_known_chains() -> None:
    rng = np.random.default_rng(0)
    iid = rng.normal(size=(4, 1000))
    assert abs(mcmc.rhat(iid) - 1.0) < 0.01
    assert 3000 < mcmc.ess_bulk(iid) < 5000
    shifted = iid + np.array([[0.0], [0.0], [0.0], [1.0]])
    assert mcmc.rhat(shifted) > 1.1
    ar = np.zeros((4, 4000))
    for t in range(1, 4000):
        ar[:, t] = 0.9 * ar[:, t - 1] + rng.normal(size=4)
    expected = 16000 * (1 - 0.9) / (1 + 0.9)  # ~842
    assert 0.7 * expected < mcmc.ess_bulk(ar) < 1.3 * expected


def test_sampler_recovers_a_correlated_gaussian() -> None:
    cov = np.array([[1.0, 0.95], [0.95, 1.0]])
    prec = np.linalg.inv(cov)

    def logp(x: np.ndarray) -> np.ndarray:
        return np.asarray(-0.5 * np.einsum("ci,ij,cj->c", x - 3.0, prec, x - 3.0))

    out = mcmc.sample(logp, np.zeros(2), np.eye(2), np.random.default_rng(1), warmup=600, draws=1500)
    pooled = out.pooled()
    assert np.allclose(pooled.mean(axis=0), 3.0, atol=0.1)
    assert np.allclose(np.cov(pooled, rowvar=False), cov, atol=0.1)
    assert max(mcmc.rhat(out.draws[:, :, i]) for i in range(2)) < 1.01


def test_fit_runtime_for_200_posts() -> None:
    history = _history(200, seed=21)
    nb2_owner.clear_cache()
    start = time.perf_counter()
    NB2OwnerModel().fit(history, PRIOR, seed=1)
    elapsed = time.perf_counter() - start
    # Target < 2 s; measured ~1.0 s on an M-series laptop. Shared CI runners are about 3x slower
    # and noisy, so CI only guards against an order-of-magnitude regression.
    limit = 10.0 if os.environ.get("CI") else 3.0
    assert elapsed < limit, elapsed


def _recovery(reps: int, n_posts: int) -> dict[str, dict[str, float]]:
    truth = Truth()
    true = {
        "a_oon": truth.a_oon,
        "log_phi": truth.log_phi,
        "a_in": truth.a_in,
        "reply_in_level": truth.a_in + math.log(truth.rho_th),
    }
    hits: dict[str, list[bool]] = {k: [] for k in true}
    err: dict[str, list[float]] = {k: [] for k in true}
    for rep in range(reps):
        fitted = NB2OwnerModel().fit(_history(n_posts, seed=500 + rep, truth=truth), PRIOR, seed=rep)
        assert isinstance(fitted, FittedOwnerNB2)
        d = fitted.draws
        post = {
            "a_oon": d["a_oon"],
            "log_phi": d["log_phi"],
            "a_in": d["a_in"],
            "reply_in_level": d["a_in"] + np.log(d["rho_th"]),
        }
        for k, v in post.items():
            lo, hi = np.quantile(v, [0.1, 0.9])
            hits[k].append(bool(lo <= true[k] <= hi))
            err[k].append(float(np.median(v)) - true[k])
    return {k: {"coverage80": float(np.mean(hits[k])), "mean_error": float(np.mean(err[k]))} for k in true}


def test_owner_model_recovers_known_intercepts_and_dispersion() -> None:
    """Replicated: a non-prior truth (a_oon = log 400 against a prior mean of log 100, phi = 5)."""
    out = _recovery(reps=6, n_posts=120)
    assert abs(out["a_oon"]["mean_error"]) < 0.15, out
    assert abs(out["log_phi"]["mean_error"]) < 0.35, out
    assert abs(out["reply_in_level"]["mean_error"]) < 0.25, out
    for k in ("a_oon", "log_phi", "reply_in_level"):
        assert out[k]["coverage80"] >= 3 / 6, out


@pytest.mark.slow
def test_owner_model_recovery_over_many_replicates() -> None:
    out = _recovery(reps=60, n_posts=120)
    print("recovery over 60 replicates:", out)
    assert abs(out["a_oon"]["mean_error"]) < 0.1, out
    assert abs(out["log_phi"]["mean_error"]) < 0.25, out
    for k in ("a_oon", "log_phi", "a_in", "reply_in_level"):
        assert 0.65 <= out[k]["coverage80"] <= 0.95, out  # binomial band for n = 60 is ~[0.68, 0.90]


@pytest.mark.slow
def test_sbc_owner_model() -> None:
    """SBC for the current code version; the release gate reads the recorded result."""
    result = run_sbc(PRIOR, n_reps=300, n_posts=30, seed=0)
    print("SBC p-values:", result.p_values)
    assert result.passed, result.p_values
    record = SBC_RESULTS.get(result.version)
    assert record is not None, f"record the SBC result for {result.version} in socialmgr/modelling/sbc.py"
    assert record.passed and record.n_reps == result.n_reps and record.n_posts == result.n_posts
    assert abs(record.min_p - result.min_p) < 1e-3


class _UrlExample(TrainingExample):
    """A training example that carries ``has_url`` (TrainingExample doesn't yet; see the report)."""

    has_url: bool = False


def test_theta_url_is_estimated_when_training_carries_has_url() -> None:
    rng = np.random.default_rng(13)
    truth = Truth(reply_share=0.0, f_growth=1.0)
    examples = []
    for p in simulate(truth, 120, rng):
        url = bool(rng.uniform() < 0.4)
        y = p.example.outcome_i48 * (math.exp(-1.2) if url else 1.0)  # a strong link penalty
        examples.append(_UrlExample(**{**p.example.model_dump(), "outcome_i48": round(y), "has_url": url}))
    fitted = NB2OwnerModel().fit(examples, PRIOR, seed=2)
    assert isinstance(fitted, FittedOwnerNB2)
    assert "theta_url" in fitted.draws and "theta_url" in fitted.diagnostics.rhat
    assert abs(float(np.median(fitted.draws["theta_url"])) + 1.2) < 0.35
    plain = ForecastSubject(subject_id="post:1", created_at=T, type="original", followers_at_post=300)
    linked = plain.model_copy(update={"has_url": True})
    assert np.median(fitted.predict(linked).samples) < 0.5 * np.median(fitted.predict(plain).samples)
