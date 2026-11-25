"""The run orchestrator (IMPLEMENTATION_PLAN.md §6 "At each run", §9 M1).

A run is an ordered list of named steps. Each step commits on its own and records its result in
``runs.steps``, so an interrupted run resumes at the first step that didn't finish: collection that
already committed is never repeated (and re-reads in the same UTC day aren't billed again anyway,
see ``pipeline._bill``). Later milestones add steps (monitors, follower Monte Carlo, experiment
readouts) by registering them; nothing else changes.
"""

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Run
from socialmgr.modelling.regimes import Fetcher
from socialmgr.services import briefing, labels, pipeline, planned, regimes
from socialmgr.services.plugins import ActivePlugins

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunContext:
    session: Session
    settings: Settings
    plugins: ActivePlugins
    run: Run
    clock: Clock
    regime_fetcher: Fetcher | None = None
    caveats: list[str] = field(default_factory=list)

    def result(self, step: str) -> dict[str, Any]:
        return dict((self.run.steps or {}).get(step, {}).get("result", {}))


StepFn = Callable[[RunContext], dict[str, Any]]


@dataclass(frozen=True)
class RunStep:
    name: str
    fn: StepFn
    summary: str


class StepRegistry:
    """Ordered steps. ``register(..., after=...)`` lets later milestones slot steps in."""

    def __init__(self) -> None:
        self._steps: list[RunStep] = []

    def register(self, name: str, summary: str, after: str | None = None) -> Callable[[StepFn], StepFn]:
        def deco(fn: StepFn) -> StepFn:
            step = RunStep(name, fn, summary)
            if after is None:
                self._steps.append(step)
            else:
                idx = [s.name for s in self._steps].index(after)
                self._steps.insert(idx + 1, step)
            return fn

        return deco

    def steps(self, skip: frozenset[str] = frozenset()) -> list[RunStep]:
        return [s for s in self._steps if s.name not in skip]

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._steps]


STEPS = StepRegistry()


@STEPS.register("collect", "Refresh access and collect posts, metrics and followers")
def _collect(ctx: RunContext) -> dict[str, Any]:
    s = pipeline.collect(ctx.session, ctx.settings, ctx.plugins, ctx.run)
    return {
        "new_posts": s.new_posts,
        "snapshots": s.snapshots,
        "cost_usd": s.cost_usd,
        "skipped": s.skipped,
        "caveats": list(s.caveats),
    }


@STEPS.register("regimes", "Check X's published ranker parameters for changes")
def _regimes(ctx: RunContext) -> dict[str, Any]:
    fetcher = ctx.regime_fetcher if ctx.settings.run.regime_check else None
    s = regimes.check_regimes(ctx.session, ctx.settings, fetcher, ctx.clock())
    return {"seeded": s.seeded, "opened": s.opened, "caveats": s.caveats}


@STEPS.register("match_planned", "Match new posts to planned posts")
def _match(ctx: RunContext) -> dict[str, Any]:
    s = planned.match_planned(ctx.session, ctx.settings, ctx.run.cutoff_at)
    return {"matched": s.matched, "missed": s.missed}


@STEPS.register("label", "Label new posts (rules, then the LLM labeller)")
def _label(ctx: RunContext) -> dict[str, Any]:
    s = labels.label_posts(ctx.session, ctx.settings, ctx.plugins, ctx.clock())
    return {
        "rule_labelled": s.rule_labelled,
        "llm_labelled": s.llm_labelled,
        "llm_pending": s.llm_pending,
        "near_duplicates": s.near_duplicates,
        "caveats": s.caveats,
    }


@STEPS.register("freeze_as_of", "Freeze as-of forecasts for posts seen for the first time")
def _freeze(ctx: RunContext) -> dict[str, Any]:
    return {"frozen": pipeline.freeze_as_of_forecasts(ctx.session, ctx.settings, ctx.plugins, ctx.clock())}


@STEPS.register("score", "Score posts that reached 48 hours")
def _score(ctx: RunContext) -> dict[str, Any]:
    s = pipeline.score_matured(ctx.session, ctx.settings, ctx.run, ctx.clock())
    return {"scored": s.scored, "late_maturity": s.late_maturity, "labels": s.labels}


@STEPS.register("experiments", "Experiment guardrails, week-boundary readouts and e-LOND")
def _experiments(ctx: RunContext) -> dict[str, Any]:
    from socialmgr.experiments import monitor

    return monitor.run_step(ctx.session, ctx.settings, ctx.run, ctx.clock())


@STEPS.register("fit_curve", "Refit the maturity curve from repeated captures")
def _fit_curve(ctx: RunContext) -> dict[str, Any]:
    curve = pipeline.fit_curve(ctx.session, ctx.settings, ctx.run, ctx.clock())
    return {"curve_version": curve.version, "n_pairs": curve.n_pairs}


@STEPS.register("briefing", "Write the run briefing")
def _briefing(ctx: RunContext) -> dict[str, Any]:
    b = briefing.write_briefing(ctx.session, ctx.settings, ctx.run, ctx.clock())
    return {"briefing_id": b.id}


@STEPS.register("backup", "Back up the database")
def _backup(ctx: RunContext) -> dict[str, Any]:
    from socialmgr.services import backups

    if not ctx.settings.run.backup:
        return {"skipped": True}
    ctx.session.commit()  # the backup must contain everything up to here
    try:
        path = backups.backup(ctx.settings, now=ctx.clock())
        pruned = backups.prune(ctx.settings)
    except OSError as exc:  # a full or missing backup folder mustn't block tomorrow's collection
        return {"error": str(exc), "caveats": [f"Backup failed: {exc}. Check SOCIALMGR_BACKUP_DIR."]}
    return {"path": str(path), "pruned": len(pruned)}


class RunInProgress(RuntimeError):
    pass


class StepFailed(RuntimeError):
    def __init__(self, run_id: int, step: str, error: str) -> None:
        self.run_id, self.step, self.error = run_id, step, error
        super().__init__(f"run {run_id} stopped at step '{step}': {error}. Run again to resume.")


@contextmanager
def run_lock(settings: Settings) -> Iterator[None]:
    """One run at a time per data folder (CLI, agent and web all take this lock)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(settings.data_dir / "run.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunInProgress("another run is in progress") from exc
        yield
    finally:
        os.close(fd)


def _stale_before_collect(run: Run, now: datetime) -> bool:
    collected = (run.steps or {}).get("collect", {}).get("status") == "done"
    return not collected and run.cutoff_at.astimezone(UTC).date() < now.astimezone(UTC).date()


def unfinished_run(session: Session, workspace_id: int) -> Run | None:
    return session.scalar(
        select(Run)
        .where(Run.workspace_id == workspace_id, Run.status.in_(("running", "failed")))
        .order_by(Run.id.desc())
        .limit(1)
    )


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    resumed: bool
    cutoff_at: datetime
    steps: dict[str, Any]


def _mark(run: Run, step: str, **fields: Any) -> None:
    steps = dict(run.steps or {})
    steps[step] = {**steps.get(step, {}), **fields}
    run.steps = steps  # reassign so the JSON column is flagged dirty


def execute(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    *,
    trigger: str = "cli",
    clock: Clock = utc_now,
    cutoff: datetime | None = None,
    regime_fetcher: Fetcher | None = None,
    skip: frozenset[str] = frozenset(),
    registry: StepRegistry = STEPS,
) -> RunOutcome:
    """Resume the unfinished run if there is one, else start a new one; run every step not yet done."""
    pipeline.ensure_accounts(session, settings, clock())
    run = unfinished_run(session, settings.workspace_id)
    if run is not None and _stale_before_collect(run, clock()):
        # Collecting now under an old cutoff would stamp today's counts with yesterday's time
        # (a look-ahead for posts in between) and bill under the wrong day: drop it and start fresh.
        run.status = "abandoned"
        run.finished_at = clock()
        session.commit()
        run = None
    resumed = run is not None
    if run is None:
        run = pipeline.start_run(session, settings, trigger=trigger, cutoff=cutoff or clock(), now=clock())
    else:
        run.status = "running"
    session.commit()

    ctx = RunContext(session, settings, plugins, run, clock, regime_fetcher)
    for step in registry.steps(skip):
        if (run.steps or {}).get(step.name, {}).get("status") == "done":
            continue
        started = clock()
        try:
            result = step.fn(ctx)
        except Exception as exc:
            session.rollback()
            _mark(
                run, step.name, status="failed", error=f"{type(exc).__name__}: {exc}", at=clock().isoformat()
            )
            run.status = "failed"
            session.commit()
            raise StepFailed(run.id, step.name, str(exc)) from exc
        _mark(
            run,
            step.name,
            status="done",
            result=result,
            started_at=started.isoformat(),
            finished_at=clock().isoformat(),
        )
        session.commit()
    pipeline.finish_run(session, run, now=clock())
    session.commit()
    return RunOutcome(run_id=run.id, resumed=resumed, cutoff_at=run.cutoff_at, steps=dict(run.steps or {}))


def run_now(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    *,
    trigger: str = "cli",
    clock: Clock = utc_now,
    regime_fetcher: Fetcher | None = None,
) -> list[RunOutcome]:
    """What ``socialmgr run`` and the agent's ``start_run`` do: under the run lock, finish any
    interrupted run, then start a fresh one if the resumed run's data is from an earlier UTC day."""
    outcomes = []
    with run_lock(settings):
        outcome = execute(
            session, settings, plugins, trigger=trigger, clock=clock, regime_fetcher=regime_fetcher
        )
        outcomes.append(outcome)
        if outcome.resumed and outcome.cutoff_at.astimezone(UTC).date() < clock().astimezone(UTC).date():
            outcomes.append(
                execute(
                    session, settings, plugins, trigger=trigger, clock=clock, regime_fetcher=regime_fetcher
                )
            )
    return outcomes
