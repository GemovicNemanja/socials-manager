"""Fixture workspace: simulate runs over the fake providers' history with the real orchestrator."""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Forecast, Post, ScoreRow
from socialmgr.labelling.llm import NoLabeler
from socialmgr.providers.fake.provider import DEFAULT_START
from socialmgr.services import pipeline, runs
from socialmgr.services.plugins import ActivePlugins

RUN_HOUR_UTC = 6
FIXTURE_SKIP = frozenset({"backup"})


@dataclass(frozen=True)
class FixtureSummary:
    runs: int
    posts: int
    forecasts: int
    scores: int


def default_until(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    until = now.replace(hour=RUN_HOUR_UTC, minute=0, second=0, microsecond=0)
    return until if until <= now else until - timedelta(days=1)


def run_times(
    start: datetime, until: datetime, gap: timedelta | Sequence[timedelta] = timedelta(days=1)
) -> list[datetime]:
    """Run cutoffs from the day after ``start`` to ``until``: a fixed gap, or a cycle of gaps."""
    gaps = [gap] if isinstance(gap, timedelta) else list(gap)
    cutoff = start.replace(hour=RUN_HOUR_UTC, minute=0, second=0, microsecond=0) + timedelta(days=1)
    times, i = [], 0
    while cutoff <= until:
        times.append(cutoff)
        cutoff += gaps[i % len(gaps)]
        i += 1
    return times


def load_fixture(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    until: datetime,
    start: datetime = DEFAULT_START,
    gap: timedelta | Sequence[timedelta] = timedelta(days=1),
) -> FixtureSummary:
    """Run every step but the backup at each cutoff, with LLM labelling off (no network)."""
    plugins = dataclasses.replace(plugins, labeler=NoLabeler())
    pipeline.ensure_accounts(session, settings, start)
    times = run_times(start, until, gap)
    for cutoff in times:
        runs.execute(
            session,
            settings,
            plugins,
            trigger="fixture",
            clock=lambda c=cutoff: c,  # type: ignore[misc]
            cutoff=cutoff,
            skip=FIXTURE_SKIP,
        )
    return FixtureSummary(
        runs=len(times),
        posts=session.scalar(select(func.count(Post.id))) or 0,
        forecasts=session.scalar(select(func.count(Forecast.id))) or 0,
        scores=session.scalar(select(func.count(ScoreRow.id))) or 0,
    )
