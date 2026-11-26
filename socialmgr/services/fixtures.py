"""Fixture workspace: simulate runs over the fake providers' history with the real orchestrator."""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.core.types import FetchedPost
from socialmgr.db.models import Forecast, PlannedSlot, Post, ScoreRow
from socialmgr.experiments import engine
from socialmgr.experiments.specs import MAX_DAYS_AHEAD
from socialmgr.labelling.llm import NoLabeler
from socialmgr.providers.fake.provider import DEFAULT_START, FakeOwnerProvider
from socialmgr.services import pipeline, runs
from socialmgr.services.plugins import ActivePlugins

RUN_HOUR_UTC = 6
FIXTURE_SKIP = frozenset({"backup"})
# The follower step (4,000 Monte Carlo paths, ~0.5 s) and the model review (a backtest, seconds) are
# the slow steps. With ``full`` they run as in production for the fixture's last days (follower step)
# and last run (review): enough for follower forecasts to be frozen and scored and for one promotion
# review. Without it (most tests) they are skipped, so building a fixture stays fast.
FOLLOWER_DAYS = timedelta(days=10)
HEAVY = frozenset({"followers", "review_model"})


def _skip(cutoff: datetime, until: datetime, full: bool) -> frozenset[str]:
    if not full:
        return FIXTURE_SKIP | HEAVY
    skip = set(FIXTURE_SKIP)
    if cutoff <= until - FOLLOWER_DAYS:
        skip.add("followers")
    if cutoff + timedelta(days=1) <= until:
        skip.add("review_model")
    return frozenset(skip)


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
    full: bool = False,
) -> FixtureSummary:
    """Run every step but the backup at each cutoff, with LLM labelling off (no network).

    ``full`` also runs the follower step and the model review near the end (see ``HEAVY``)."""
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
            skip=_skip(cutoff, until, full),
        )
    return FixtureSummary(
        runs=len(times),
        posts=session.scalar(select(func.count(Post.id))) or 0,
        forecasts=session.scalar(select(func.count(Forecast.id))) or 0,
        scores=session.scalar(select(func.count(ScoreRow.id))) or 0,
    )


# ---------------------------------------------------------------- experiment demo (test fixture only)

DEMO_SUGGESTIONS = ("E03", "E06")  # a two-factor factorial: outcome-first hook x image on non-demo posts
DEMO_DAYS = 21  # three experiment weeks before ``until``: the week-1 and week-2 readouts exist
DEMO_SLOT_TIMES = (time(7), time(11), time(16))  # the fake owner's posting hours (UTC accounts)
DEMO_EFFECTS = {"E03.hook": 1.6, "E06.image": 1.0}  # true multipliers in the simulated world


def _demo_plugins(session: Session, plugins: ActivePlugins) -> ActivePlugins:
    """The fake owner follows each slot's instructions: a post within 30 minutes of an experiment
    slot is an original, and its I48 carries the true effect of the slot's arms (DEMO_EFFECTS)."""

    def hook(post: FetchedPost, i48: float) -> tuple[FetchedPost, float]:
        slot = session.scalar(
            select(PlannedSlot).where(
                PlannedSlot.planned_at >= post.created_at - timedelta(minutes=30),
                PlannedSlot.planned_at <= post.created_at + timedelta(minutes=30),
                PlannedSlot.state.in_(("planned", "posted")),
                PlannedSlot.experiment_id.is_not(None),
            )
        )
        if slot is None:
            return post, i48
        lift = 1.0
        for key, arm in engine.effective_arms(session, slot.id).items():
            lift *= DEMO_EFFECTS.get(key, 1.0) if arm else 1.0
        return post.model_copy(update={"type": "original"}), i48 * lift

    return dataclasses.replace(plugins, labeler=NoLabeler(), providers=(FakeOwnerProvider(post_hook=hook),))


def load_experiment_demo(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    until: datetime,
    start: datetime = DEFAULT_START,
    full: bool = False,
) -> FixtureSummary:
    """The fixture account plus three weeks of a running E03 x E06 factorial (end-to-end tests only).

    Runs the plain fixture up to ``until - 21 days``; then the owner proposes E03 and E06, and each
    day slots are registered up to 7 days ahead (never past ``until``'s date, so a test can register
    the days after it) before the next daily run. Everything goes through the real engine and
    orchestrator; only the fake owner's posts react to the slots' arms.
    """
    begin = until - timedelta(days=DEMO_DAYS)
    load_fixture(session, settings, plugins, until=begin, start=start)
    world = _demo_plugins(session, plugins)
    proposed_at = begin + timedelta(hours=1)
    for key in DEMO_SUGGESTIONS:
        engine.propose_experiment(
            session, settings, world, actor="owner", now=proposed_at, suggestion_key=key
        )
    session.flush()
    registered: set[date] = set()
    cut = begin
    while cut < until:
        now = cut + timedelta(hours=1)
        days = [now.date() + timedelta(days=k) for k in range(1, MAX_DAYS_AHEAD + 1)]
        days = [d for d in days if d <= until.date() and d not in registered]
        if days:
            plans = engine.day_plans(days, DEMO_SLOT_TIMES)
            engine.register_slots(session, settings, world, plans, now=now, actor="owner")
            registered |= set(days)
        cut += timedelta(days=1)
        runs.execute(
            session,
            settings,
            world,
            trigger="fixture",
            clock=lambda c=cut: c,  # type: ignore[misc]
            cutoff=cut,
            skip=_skip(cut, until, full),
        )
    return FixtureSummary(
        runs=len(run_times(start, until)),
        posts=session.scalar(select(func.count(Post.id))) or 0,
        forecasts=session.scalar(select(func.count(Forecast.id))) or 0,
        scores=session.scalar(select(func.count(ScoreRow.id))) or 0,
    )
