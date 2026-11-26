"""Run orchestrator: step order, resume after an interruption, billing once per UTC day, the run lock."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.db import migrate
from socialmgr.db.models import Briefing, Cost, MetricSnapshot, ResourceRead, Run
from socialmgr.db.session import make_engine, session_factory
from socialmgr.services import runs
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings

T0 = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


def _settings(tmp_path: Path, **run: Any) -> Settings:
    return make_settings(
        tmp_path,
        plugins=PluginConfig(labeler="none"),
        run=RunConfig(regime_check=False, backup=run.pop("backup", False), **run),
    )


def _factory(settings: Settings) -> sessionmaker[Session]:
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return session_factory(engine)


def test_steps_run_in_plan_order(tmp_path: Path) -> None:
    assert runs.STEPS.names == [
        "collect",
        "regimes",
        "match_planned",
        "label",
        "freeze_as_of",
        "score",
        "monitors",
        "experiments",
        "fit_curve",
        "followers",
        "briefing",
        "backup",
    ]
    settings = _settings(tmp_path)
    with _factory(settings)() as session:
        outcome = runs.execute(session, settings, load_plugins(settings), clock=lambda: T0)
        assert not outcome.resumed
        assert [k for k, v in outcome.steps.items() if v["status"] == "done"] == runs.STEPS.names
        run = session.get(Run, outcome.run_id)
        assert run is not None and run.status == "finished"
        assert session.scalar(select(Briefing.summary).where(Briefing.run_id == run.id))


class Boom(RuntimeError):
    pass


def test_interrupted_run_resumes_without_recollecting_or_rebilling(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plugins = load_plugins(settings)
    registry = runs.StepRegistry()
    for step in runs.STEPS.steps():
        registry.register(step.name, step.summary)(step.fn)
    calls = {"n": 0}

    @registry.register("explode", "fails once", after="label")
    def _explode(ctx: runs.RunContext) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise Boom("disk full")
        return {}

    with _factory(settings)() as session:
        with pytest.raises(runs.StepFailed, match="explode"):
            runs.execute(session, settings, plugins, clock=lambda: T0, registry=registry)
        failed = session.scalar(select(Run))
        assert failed is not None and failed.status == "failed"
        assert failed.steps["collect"]["status"] == "done"
        assert failed.steps["explode"]["status"] == "failed"
        snapshots = session.scalar(select(func.count(MetricSnapshot.id)))
        spend = session.scalar(select(func.sum(Cost.amount_usd)))

        later = T0 + timedelta(hours=2)  # same UTC day
        outcome = runs.execute(session, settings, plugins, clock=lambda: later, registry=registry)
        assert outcome.resumed and outcome.run_id == failed.id
        assert session.scalar(select(func.count(Run.id))) == 1
        assert session.scalar(select(func.count(MetricSnapshot.id))) == snapshots  # collect didn't re-run
        assert session.scalar(select(func.sum(Cost.amount_usd))) == spend  # nothing billed twice
        assert session.get(Run, failed.id).status == "finished"  # type: ignore[union-attr]


def test_second_run_same_utc_day_bills_only_new_reads(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plugins = load_plugins(settings)
    with _factory(settings)() as session:
        runs.execute(session, settings, plugins, clock=lambda: T0)
        reads = session.scalar(select(func.count(ResourceRead.id)))
        first = session.scalar(select(func.sum(Cost.amount_usd)))
        assert reads and first and first > 0
        # Six hours later, the same UTC day: the 07:00 and 11:00 posts are new; every other read was billed.
        runs.execute(session, settings, plugins, clock=lambda: T0 + timedelta(hours=6))
        second_run_cost = session.scalar(select(func.sum(Cost.amount_usd)).where(Cost.run_id == 2)) or 0
        assert second_run_cost == pytest.approx(0.001 * 2)
        # Next day: everything is billable again.
        runs.execute(session, settings, plugins, clock=lambda: T0 + timedelta(days=1))
        third = session.scalar(select(func.sum(Cost.amount_usd)).where(Cost.run_id == 3)) or 0
        assert third > second_run_cost


def test_monthly_cap_skips_collection(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        monthly_cap_usd=0.0,
        plugins=PluginConfig(labeler="none"),
        run=RunConfig(regime_check=False, backup=False),
    )
    with _factory(settings)() as session:
        outcome = runs.execute(session, settings, load_plugins(settings), clock=lambda: T0)
        assert outcome.steps["collect"]["result"]["skipped"] is True
        assert session.scalar(select(func.count(MetricSnapshot.id))) == 0
        assert "cap" in session.scalar(select(Briefing.summary))  # type: ignore[operator]


def test_run_lock_is_exclusive(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with runs.run_lock(settings), pytest.raises(runs.RunInProgress), runs.run_lock(settings):
        pass
    with runs.run_lock(settings):  # released
        pass


def test_resumed_run_from_an_earlier_day_is_followed_by_a_fresh_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plugins = load_plugins(settings)
    with _factory(settings)() as session:
        failing = runs.StepRegistry()
        failing.register("collect", "")(runs.STEPS.steps()[0].fn)

        @failing.register("explode", "")
        def _explode(ctx: runs.RunContext) -> dict[str, Any]:
            raise Boom("killed")

        with pytest.raises(runs.StepFailed):
            runs.execute(session, settings, plugins, clock=lambda: T0, registry=failing)
        outcomes = runs.run_now(session, settings, plugins, clock=lambda: T0 + timedelta(days=2))
        assert [o.resumed for o in outcomes] == [True, False]
        assert outcomes[1].cutoff_at == T0 + timedelta(days=2)


def test_backup_step_writes_and_prunes(tmp_path: Path) -> None:
    settings = _settings(tmp_path, backup=True, backup_keep_daily=2, backup_keep_weekly=0)
    plugins = load_plugins(settings)
    with _factory(settings)() as session:
        for day in range(4):
            runs.execute(session, settings, plugins, clock=lambda d=day: T0 + timedelta(days=d))  # type: ignore[misc]
    files = sorted(settings.resolved_backup_dir.glob("socialmgr-*.sqlite3"))
    assert [f.name for f in files] == [
        "socialmgr-20261103T060000Z.sqlite3",
        "socialmgr-20261104T060000Z.sqlite3",
    ]
