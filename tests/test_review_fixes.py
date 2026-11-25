"""Regression tests for the M1 code review: each test names the defect it pins down."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.core.types import CollectionBatch, CollectionRequest
from socialmgr.db import migrate
from socialmgr.db.models import FollowerSnapshot, Forecast, PlannedSlot, Post, Run
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.providers.fake.provider import FakeOwnerProvider
from socialmgr.services import pipeline, planned, runs
from socialmgr.services.fixtures import load_fixture
from socialmgr.services.plugins import ActivePlugins, load_plugins
from tests.conftest import make_settings

T = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    run = kw.pop("run", RunConfig(regime_check=False, backup=False))
    return make_settings(tmp_path, plugins=PluginConfig(labeler="none"), run=run, **kw)


def _session(settings: Settings) -> Session:
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return session_factory(engine)()


def _plugins(settings: Settings, provider: Any = None) -> ActivePlugins:
    p = dataclasses.replace(load_plugins(settings), labeler=NoLabeler())
    return dataclasses.replace(p, providers=(provider,)) if provider else p


def _run(session: Session, settings: Settings, plugins: ActivePlugins, at: datetime) -> runs.RunOutcome:
    return runs.execute(session, settings, plugins, clock=lambda: at, cutoff=at)


def test_skipped_collection_carries_the_follower_count_forward(tmp_path: Path) -> None:
    """Review #1: a run that wrote no follower count must not make later posts forecast at F=0."""
    settings = _settings(tmp_path)
    with _session(settings) as session:
        plugins = _plugins(settings)
        load_fixture(session, settings, plugins, until=T)
        count = session.scalar(
            select(FollowerSnapshot.followers).order_by(FollowerSnapshot.id.desc()).limit(1)
        )
        capped = settings.model_copy(update={"monthly_cap_usd": 0.0})
        assert _run(session, capped, plugins, T + timedelta(days=1)).steps["collect"]["result"]["skipped"]
        _run(session, settings, plugins, T + timedelta(days=2))
        new = session.scalars(select(Forecast))
        late = [
            fc
            for fc in new
            if fc.input_manifest["cutoff"]
            == (T + timedelta(days=1)).isoformat()  # skipped run is the snapshot
        ]
        assert late, "posts made after the skipped run should use it as their snapshot"
        for fc in late:
            assert fc.input_manifest["subject"]["followers_at_post"] == count
            assert fc.input_manifest["followers_source"] == "snapshot"


class LateClockProvider(FakeOwnerProvider):
    """Like the real X provider: captures are stamped a few minutes after the run's cutoff."""

    def collect(self, request: CollectionRequest) -> CollectionBatch:
        batch = super().collect(request)
        shifted = tuple(
            m.model_copy(update={"fetched_at": m.fetched_at + timedelta(minutes=3)}) for m in batch.metrics
        )
        return batch.model_copy(update={"metrics": shifted})


def test_a_runs_own_captures_count_when_fetched_after_its_cutoff(tmp_path: Path) -> None:
    """Review #2: selecting captures by fetched_at <= cutoff dropped every run's own captures."""
    a, b = _settings(tmp_path / "a"), _settings(tmp_path / "b")
    with _session(a) as sa, _session(b) as sb:
        load_fixture(sa, a, _plugins(a), until=T)
        load_fixture(sb, b, _plugins(b, LateClockProvider()), until=T)
        for s in (sa, sb):
            last = pipeline.last_finished_run(s, 1)
            assert last is not None
            assert len(pipeline.matured_examples(s, 1, last.cutoff_at)) > 0
        last_a, last_b = pipeline.last_finished_run(sa, 1), pipeline.last_finished_run(sb, 1)
        assert last_a is not None and last_b is not None
        assert len(pipeline.matured_examples(sa, 1, last_a.cutoff_at)) == len(
            pipeline.matured_examples(sb, 1, last_b.cutoff_at)
        )
        assert len(pipeline.capture_pairs(sa, 1, last_a.cutoff_at)) == len(
            pipeline.capture_pairs(sb, 1, last_b.cutoff_at)
        )


class FailOnce(FakeOwnerProvider):
    calls = 0

    def collect(self, request: CollectionRequest) -> CollectionBatch:
        FailOnce.calls += 1
        if FailOnce.calls == 1:
            raise RuntimeError("network down")
        return super().collect(request)


def test_a_stale_run_that_never_collected_is_abandoned_not_resumed(tmp_path: Path) -> None:
    """Review #3: resuming collection days later under the old cutoff leaks counts and double-bills."""
    settings = _settings(tmp_path)
    plugins = _plugins(settings, FailOnce())
    with _session(settings) as session:
        with pytest.raises(runs.StepFailed):
            _run(session, settings, plugins, T)
        outcome = runs.run_now(session, settings, plugins, clock=lambda: T + timedelta(days=2))[0]
        assert not outcome.resumed and outcome.cutoff_at == T + timedelta(days=2)
        assert session.scalar(select(Run.status).where(Run.id == 1)) == "abandoned"


def test_same_day_resume_still_resumes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    FailOnce.calls = 0
    plugins = _plugins(settings, FailOnce())
    with _session(settings) as session:
        with pytest.raises(runs.StepFailed):
            _run(session, settings, plugins, T)
        outcome = runs.execute(session, settings, plugins, clock=lambda: T + timedelta(hours=1))
        assert outcome.resumed and outcome.run_id == 1


def test_a_post_published_before_registration_never_matches(tmp_path: Path) -> None:
    """Review #4: a 'live' forecast must predate its post."""
    settings = _settings(tmp_path)
    plugins = _plugins(settings)
    with _session(settings) as session:
        load_fixture(session, settings, plugins, until=T)
        # Collect the posts of Nov 1 at 20:00, then register a slot 5 minutes after one of them.
        _run(session, settings, plugins, T + timedelta(hours=14))
        post = session.scalars(
            select(Post).where(Post.created_at > T).order_by(Post.created_at.desc())
        ).first()
        assert post is not None
        registered = post.created_at + timedelta(minutes=10)
        planned.register_planned_post(
            session,
            settings,
            plugins,
            planned.PlanRequest(planned_at=registered + timedelta(minutes=5), post_type=post.type),
            now=registered,
        )
        session.commit()
        _run(session, settings, plugins, T + timedelta(days=1))
        slot = session.scalars(select(PlannedSlot)).one()
        assert slot.matched_post_id != post.id


def test_backfilled_training_examples_use_the_earliest_follower_count(tmp_path: Path) -> None:
    """Review #6: posts first seen at the first run must not train the model as F=0."""
    settings = _settings(tmp_path)
    with _session(settings) as session:
        load_fixture(session, settings, _plugins(settings), until=T)
        earliest = pipeline.earliest_followers(session, 1)
        assert earliest and earliest > 0
        examples = pipeline.matured_examples(session, 1, T)
        assert examples and min(e.followers_at_post for e in examples) > 0


def test_a_failing_backup_is_a_caveat_and_the_run_finishes(tmp_path: Path) -> None:
    """Review #10: a broken backup folder must not block every later run."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    settings = _settings(
        tmp_path, backup_dir=blocker / "backups", run=RunConfig(regime_check=False, backup=True)
    )
    with _session(settings) as session:
        outcome = _run(session, settings, _plugins(settings), T)
        assert outcome.steps["backup"]["status"] == "done"
        assert "Backup failed" in outcome.steps["backup"]["result"]["caveats"][0]
        assert session.scalar(select(Run.status)) == "finished"
