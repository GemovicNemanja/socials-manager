"""Fixture workspace: simulate daily runs over the fake providers' history."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.providers.fake.provider import DEFAULT_START
from socialmgr.services import pipeline
from socialmgr.services.plugins import ActivePlugins

RUN_HOUR_UTC = 6


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


def load_fixture(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    until: datetime,
    start: datetime = DEFAULT_START,
    gap: timedelta = timedelta(days=1),
) -> FixtureSummary:
    """Run collect → freeze → score once per ``gap`` from ``start`` to ``until``."""
    pipeline.ensure_accounts(session, settings, start)
    cutoff = start.replace(hour=RUN_HOUR_UTC, minute=0, second=0, microsecond=0) + timedelta(days=1)
    runs = posts = forecasts = scores = 0
    while cutoff <= until:
        run = pipeline.start_run(session, settings, trigger="fixture", cutoff=cutoff, now=cutoff)
        posts += pipeline.collect(session, settings, plugins, run).new_posts
        forecasts += pipeline.freeze_as_of_forecasts(session, settings, plugins, now=cutoff)
        scores += pipeline.score_matured(session, settings, run, now=cutoff)
        pipeline.finish_run(session, run, now=cutoff)
        runs += 1
        cutoff += gap
    return FixtureSummary(runs=runs, posts=posts, forecasts=forecasts, scores=scores)
