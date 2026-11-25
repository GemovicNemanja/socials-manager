"""M1 acceptance criteria (IMPLEMENTATION_PLAN.md §9), checked on a simulated clock."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.core.types import AccountRef, CollectionRequest
from socialmgr.db import migrate
from socialmgr.db.models import Forecast, MetricSnapshot, PlannedSlot, Post, Run, ScoreRow
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.modelling import maturity
from socialmgr.providers.fake.provider import DEFAULT_START, FakeOwnerProvider
from socialmgr.services import draws, pipeline, planned, runs
from socialmgr.services.fixtures import load_fixture, run_times
from socialmgr.services.hashing import sha256_json
from socialmgr.services.plugins import ActivePlugins, load_plugins
from tests.conftest import make_settings

UNTIL = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
IRREGULAR = [timedelta(days=d) for d in (1, 3, 2, 1, 3, 3, 2, 1, 2)]


def _settings(tmp_path: Path) -> Settings:
    return make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )


def _session(settings: Settings) -> Session:
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return session_factory(engine)()


def test_irregular_gaps_every_post_matures_and_is_scored(tmp_path: Path) -> None:
    """Gaps of 1-3 days: every post older than 48h + one gap has a capture at >= 48h and a score."""
    settings = _settings(tmp_path)
    with _session(settings) as session:
        load_fixture(session, settings, load_plugins(settings), until=UNTIL, gap=IRREGULAR)
        last_cutoff = session.scalar(select(func.max(Run.cutoff_at)))
        assert last_cutoff is not None
        posts = session.scalars(select(Post)).all()
        assert len(posts) > 100
        for post in posts:
            if post.created_at > last_cutoff - timedelta(hours=48):
                continue  # can't have matured yet
            first_mature = session.scalar(
                select(func.min(MetricSnapshot.age_s)).where(
                    MetricSnapshot.post_id == post.id, MetricSnapshot.age_s >= 48 * 3600
                )
            )
            assert first_mature is not None, f"post {post.id} has no maturity capture"
            assert first_mature <= (48 + 72) * 3600  # at most one 3-day gap past 48h
            score = session.scalar(
                select(ScoreRow).join(Forecast).where(Forecast.subject == f"post:{post.id}")
            )
            assert score is not None, f"post {post.id} wasn't scored"
            assert score.late_maturity is False


def test_a_long_gap_flags_late_maturity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with _session(settings) as session:
        load_fixture(
            session,
            settings,
            load_plugins(settings),
            until=UNTIL,
            gap=[timedelta(days=1)] * 5 + [timedelta(days=16)],
        )
        late = session.scalars(select(ScoreRow).where(ScoreRow.late_maturity)).all()
        assert late, "posts first captured after 14 days must be flagged"
        assert all(s.capture_age_h > 14 * 24 for s in late)
        assert all(
            s.capture_age_h <= 14 * 24
            for s in session.scalars(select(ScoreRow).where(~ScoreRow.late_maturity))
        )


def test_as_of_forecasts_recompute_and_ignore_perturbed_later_captures(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plugins = load_plugins(settings)
    with _session(settings) as session:
        load_fixture(session, settings, plugins, until=UNTIL, gap=IRREGULAR)
        frozen = {fc.id: fc.input_manifest for fc in session.scalars(select(Forecast))}

        # Perturb history after every snapshot: a late run with wildly different captures.
        late_cutoff = UNTIL + timedelta(days=1)
        run = pipeline.start_run(session, settings, "test", cutoff=late_cutoff, now=late_cutoff)
        rng = np.random.default_rng(0)
        for perturbed in session.scalars(select(Post)):
            session.add(
                MetricSnapshot(
                    workspace_id=1,
                    post_id=perturbed.id,
                    run_id=run.id,
                    provider="perturbed",
                    impressions_source="impression_count",
                    fetched_at=late_cutoff,
                    age_s=int((late_cutoff - perturbed.created_at).total_seconds()),
                    impressions=int(rng.integers(0, 10**6)),
                    likes=0,
                    reposts=0,
                    quotes=0,
                    replies=0,
                    bookmarks=0,
                )
            )
        pipeline.fit_curve(session, settings, run, late_cutoff)
        pipeline.finish_run(session, run, late_cutoff)
        session.flush()

        for fc in session.scalars(select(Forecast)):
            post = session.get(Post, int(fc.subject.split(":")[1]))
            assert post is not None
            rebuilt = pipeline.build_manifest(session, 1, post)
            stored = {k: v for k, v in frozen[fc.id].items() if k not in ("model_version", "calibrated")}
            assert rebuilt == stored, f"forecast {fc.id} manifest changed after later captures"
            assert sha256_json(frozen[fc.id]) == fc.manifest_hash
            version, _calibrated, predicted = pipeline.forecast_from_manifest(plugins, fc.input_manifest)
            assert version == fc.model_version
            assert draws.encode(predicted.samples)[0] == fc.draws_hash  # bit-for-bit


def test_planned_post_forecast_is_frozen_before_the_post_and_scored(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plugins = load_plugins(settings)
    with _session(settings) as session:
        # Runs up to Nov 10; the evening before, register the account's first original of Nov 11.
        load_fixture(session, settings, plugins, until=datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        owner = pipeline.owner_account(session, 1)
        assert owner is not None
        upcoming = FakeOwnerProvider().collect(
            CollectionRequest(
                accounts=(AccountRef(account_id=owner.id, platform="x", handle=owner.handle, role="owner"),),
                since=datetime(2026, 11, 11, tzinfo=UTC),
                until=datetime(2026, 11, 12, tzinfo=UTC),
            )
        )
        target = next(p for p in upcoming.posts if p.type == "original")
        registered_at = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
        result = planned.register_planned_post(
            session,
            settings,
            plugins,
            planned.PlanRequest(planned_at=target.created_at + timedelta(minutes=10), registered_by="agent"),
            now=registered_at,
        )
        slot_id, forecast_id = result.slot.id, result.forecast.id
        assert result.forecast.kind == "live"
        session.commit()
        for cutoff in run_times(DEFAULT_START, datetime(2026, 11, 14, 6, 0, tzinfo=UTC)):
            if cutoff <= datetime(2026, 11, 10, 6, 0, tzinfo=UTC):
                continue
            runs.execute(
                session,
                settings,
                dataclasses.replace(plugins, labeler=NoLabeler()),
                clock=lambda c=cutoff: c,  # type: ignore[misc]
                cutoff=cutoff,
            )
        slot = session.get(PlannedSlot, slot_id)
        forecast = session.get(Forecast, forecast_id)
        assert slot is not None and forecast is not None
        assert slot.state == "posted" and slot.matched_post_id is not None
        post = session.get(Post, slot.matched_post_id)
        assert post is not None and post.external_id == target.external_id
        assert forecast.frozen_at < post.created_at  # frozen before the post existed
        assert session.scalar(select(ScoreRow).where(ScoreRow.forecast_id == forecast_id)) is not None
        # The live forecast also recomputes bit-for-bit from its manifest.
        _version, _c, predicted = pipeline.forecast_from_manifest(plugins, forecast.input_manifest)
        assert draws.encode(predicted.samples)[0] == forecast.draws_hash


def test_pipeline_recovers_a_non_prior_maturity_curve(tmp_path: Path) -> None:
    """With irregular gaps the curve is identified from capture pairs (MODEL §1.3)."""
    true = maturity.Curve("truth", scale_h=6.0, shape=0.6)
    settings = _settings(tmp_path)
    plugins = load_plugins(settings)
    plugins = ActivePlugins(
        providers=(FakeOwnerProvider(curve=true),),
        prior_source=plugins.prior_source,
        champion=plugins.champion,
        challengers=plugins.challengers,
        labeler=NoLabeler(),
    )
    with _session(settings) as session:
        load_fixture(session, settings, plugins, until=UNTIL, gap=IRREGULAR)
        curve = pipeline.curve_as_of(session, 1, UNTIL)
        assert curve.version != maturity.PRIOR_CURVE_VERSION
        for age in (1.0, 6.0, 24.0, 100.0):
            assert abs(curve.g(age) / true.g(age) - 1) < 0.05, (age, curve)
        # Later forecasts carry the fitted curve version; early ones the prior.
        versions = [fc.curve_version for fc in session.scalars(select(Forecast).order_by(Forecast.id))]
        assert versions[0] == maturity.PRIOR_CURVE_VERSION and versions[-1].startswith("weibull-")
