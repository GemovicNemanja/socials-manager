"""A simulated world for experiment tests: the fake owner provider posts at 07, 11 and 16 UTC
(±20 min), and a hook makes each post an original whose I48 reacts to its slot's effective arms."""

import dataclasses
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.core.types import FetchedPost
from socialmgr.db.models import PlannedSlot
from socialmgr.db.session import make_engine, session_factory
from socialmgr.experiments import engine
from socialmgr.experiments.specs import ExperimentSpec, FactorSpec
from socialmgr.labelling.llm import NoLabeler
from socialmgr.providers.fake.provider import FakeOwnerProvider
from socialmgr.services import runs
from socialmgr.services.plugins import ActivePlugins, load_plugins
from tests.conftest import make_settings

SLOT_TIMES = [time(7), time(11), time(16)]
FIRST_CUTOFF = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)  # the session fixture's last run
NOW = FIRST_CUTOFF + timedelta(hours=1)  # 2026-11-25 07:00 UTC, after the fixture's last run
TOMORROW = date(2026, 11, 26)
Effect = Callable[[dict[str, int]], float]  # effective arms {"E03.hook": 1} -> I48 multiplier


def sim_settings(tmp_path: Any) -> Settings:
    return make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )


def open_copy(settings: Settings, fixture_db: Path) -> Session:
    """A session on a fresh copy of the session fixture workspace (runs up to FIRST_CUTOFF)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_db, settings.db_path)
    return session_factory(make_engine(settings))()


def custom_spec(key: str, *factors: FactorSpec, weeks: float = 4.0, seed: int | None = 11) -> ExperimentSpec:
    return ExperimentSpec(
        key=key, name=key, hypothesis="test", factors=list(factors), horizon_weeks=weeks, seed=seed
    )


@dataclass
class World:
    session: Session
    effect: Effect = lambda arms: 1.0

    def hook(self, post: FetchedPost, i48: float) -> tuple[FetchedPost, float]:
        slot = self.session.scalar(
            select(PlannedSlot).where(
                PlannedSlot.planned_at >= post.created_at - timedelta(minutes=30),
                PlannedSlot.planned_at <= post.created_at + timedelta(minutes=30),
                PlannedSlot.state.in_(("planned", "posted")),
                PlannedSlot.experiment_id.is_not(None),
            )
        )
        if slot is None:
            return post, i48
        arms = engine.effective_arms(self.session, slot.id)
        return post.model_copy(update={"type": "original"}), i48 * self.effect(arms)

    def plugins(self, settings: Settings) -> ActivePlugins:
        return dataclasses.replace(
            load_plugins(settings), labeler=NoLabeler(), providers=(FakeOwnerProvider(post_hook=self.hook),)
        )


@dataclass
class SimLog:
    runs: list[tuple[datetime, dict[str, Any]]] = field(default_factory=list)
    registered: set[date] = field(default_factory=set)
    refusals: list[str] = field(default_factory=list)


def register_ahead(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    now: datetime,
    log: SimLog,
    days_ahead: int = 7,
) -> None:
    today = now.date()
    days = [today + timedelta(days=k) for k in range(1, days_ahead + 1)]
    days = [d for d in days if d not in log.registered]
    if not days:
        return
    try:
        engine.register_slots(
            session, settings, plugins, engine.day_plans(days, SLOT_TIMES), now=now, actor="agent"
        )
        session.commit()
        log.registered |= set(days)
    except engine.ExperimentError as exc:
        session.rollback()
        log.refusals.append(str(exc))


def simulate(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    n_days: int,
    log: SimLog | None = None,
    start: datetime = FIRST_CUTOFF,
    register: bool = True,
    stop_when: Callable[[dict[str, Any]], bool] | None = None,
) -> SimLog:
    """Daily: register slots up to 7 days ahead an hour after the run, then run the next day."""
    log = log or SimLog()
    cut = start
    for _ in range(n_days):
        if register:
            register_ahead(session, settings, plugins, cut + timedelta(hours=1), log)
        cut += timedelta(days=1)
        out = runs.execute(
            session,
            settings,
            plugins,
            clock=lambda c=cut: c,  # type: ignore[misc]
            cutoff=cut,
            skip=frozenset({"backup"}),
        )
        result = out.steps["experiments"]["result"]
        log.runs.append((cut, result))
        if stop_when is not None and stop_when(result):
            break
    return log
