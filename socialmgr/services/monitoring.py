"""The run's monitor step (MODEL §4.3): D1 charts, D3, D2 and diagnostics, persisted per run.

The engine state is stored as the ``engine`` row of ``monitor_states``; each chart also gets a
display row so the Forecasts screen can draw its history. Alarms are recorded for the owner to
acknowledge. Charts arm at 20 matured owner posts (plan §6 eligibility).
"""

import statistics
from collections.abc import Callable
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Alarm, Forecast, MonitorState, Run
from socialmgr.modelling.monitors import (
    GoalInputs,
    MonitorReport,
    initial_state,
    run_monitors,
)
from socialmgr.modelling.monitors import MonitorState as EngineState
from socialmgr.services import draws, goals, pipeline
from socialmgr.services.follower_data import run_intervals
from socialmgr.services.model_data import scored_posts

ENGINE = "engine"
PIT_EPS = 0.5 / 1001  # PITs come from 1,000 predictive draws
DISPLAY = {"location": "d1_location", "scale": "d1_scale", "follower": "d1_followers", "aci": "d3"}
D2_WINDOW = 30

# Supplies the randomized PITs of follower gains for the intervals closed since the last monitored
# run. The follower step provides it once the follower model is fitted; without one, the follower
# chart stays disarmed.
FollowerPits = Callable[[Session, Settings, Run], tuple[list[float], bool]]


def _previous(session: Session, ws: int, before: datetime) -> tuple[EngineState | None, datetime | None]:
    row = session.execute(
        select(MonitorState, Run.cutoff_at)
        .join(Run, Run.id == MonitorState.run_id)
        .where(MonitorState.workspace_id == ws, MonitorState.chart == ENGINE, Run.cutoff_at < before)
        .order_by(Run.cutoff_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    return EngineState.from_dict(row[0].state), row[1]


def typical_gap_days(session: Session, account_id: int, until: datetime) -> float:
    """Median days between the last 10 runs that measured followers, rounded to whole days (≥ 1)."""
    gaps = [i.days for i in run_intervals(session, account_id, until)[-10:]]
    return float(max(round(statistics.median(gaps)), 1)) if gaps else 1.0


def _goal_inputs(session: Session, settings: Settings, run: Run) -> GoalInputs | None:
    goal = goals.current_goals(session, settings.workspace_id).get("posts")
    if goal is None:
        return None
    recent = scored_posts(session, settings.workspace_id, until=run.cutoff_at)[-D2_WINDOW:]
    if len(recent) < D2_WINDOW:
        return None
    hashes = {
        fc.id: fc.draws_hash
        for fc in session.scalars(select(Forecast).where(Forecast.id.in_([r.forecast_id for r in recent])))
    }
    matrix = np.vstack([draws.load(session, hashes[r.forecast_id]) for r in recent])
    return GoalInputs(draws=matrix, outcomes=np.array([r.outcome for r in recent]), goal=goal.target)


def _display_rows(
    report: MonitorReport,
) -> list[tuple[str, bool, float | None, float | None, bool, dict[str, Any]]]:
    rows = []
    for c in report.charts:
        chart = DISPLAY.get(c.name, c.name)
        if c.kind == "aci":
            stat = c.statistics.get("alpha_t")
            threshold = c.thresholds.get("alarm_level")
            detail = f"alpha_t {stat:.3f}; alarm when below 0.10 for 20 posts" if stat is not None else ""
        else:
            up, lo = c.statistics.get("upper", 0.0), c.statistics.get("lower", 0.0)
            hu, hl = c.thresholds.get("upper"), c.thresholds.get("lower")
            # Show the side closer to its threshold, as a share of it.
            if hu and hl and lo / hl > up / hu:
                stat, threshold, side = lo, hl, "down"
            else:
                stat, threshold, side = up, hu, "up"
            detail = (
                f"two-sided CUSUM, k 0.25, h {threshold:.2f}; leaning {side}"
                if threshold
                else "two-sided CUSUM"
            )
        rows.append(
            (
                chart,
                c.armed,
                stat,
                threshold,
                c.alarmed_this_run,
                {"detail": detail, "n": c.n, "statistics": c.statistics, "thresholds": c.thresholds},
            )
        )
    g = report.goal
    if g is not None:
        detail = {
            "too_hard": f"Hit rate {g.pi_bar:.0%}: goal looks too hard; suggest {g.suggestion:,.0f}",
            "too_easy": f"Hit rate {g.pi_bar:.0%}: goal looks too easy; suggest {g.suggestion:,.0f}",
        }.get(g.status, g.status.replace("_", " "))
        rows.append(
            (
                "d2",
                True,
                g.pi_bar,
                None,
                False,
                {
                    "detail": detail,
                    "display_state": "proposal" if g.proposed else "quiet",
                    "check": {k: v for k, v in g.__dict__.items()},
                },
            )
        )
    return rows


def run_step(
    session: Session, settings: Settings, run: Run, now: datetime, follower_pits: FollowerPits | None = None
) -> dict[str, Any]:
    ws = settings.workspace_id
    owner = pipeline.owner_account(session, ws)
    if owner is None:
        return {"skipped": "no owner account"}
    previous, _ = _previous(session, ws, run.cutoff_at)
    gap = typical_gap_days(session, owner.id, run.cutoff_at)
    state = previous or initial_state(gap)
    scored = scored_posts(session, ws, until=run.cutoff_at)
    new_pits = [r.pit_rand for r in scored if r.run_id == run.id]
    f_pits, f_armed = follower_pits(session, settings, run) if follower_pits else ([], False)
    new_state, report = run_monitors(
        state,
        post_pits=new_pits,
        follower_pits=f_pits,
        matured_owner_posts=len(scored),
        gap_days=gap,
        follower_armed=f_armed,
        goal=_goal_inputs(session, settings, run),
        eps=PIT_EPS,
    )
    session.add(
        MonitorState(
            workspace_id=ws,
            run_id=run.id,
            chart=ENGINE,
            armed=report.armed,
            stat=None,
            threshold=None,
            alarm=bool(report.alarms),
            state=new_state.to_dict(),
            created_at=now,
        )
    )
    for chart, armed, stat, threshold, alarm, detail in _display_rows(report):
        session.add(
            MonitorState(
                workspace_id=ws,
                run_id=run.id,
                chart=chart,
                armed=armed,
                stat=stat,
                threshold=threshold,
                alarm=alarm,
                state=detail,
                created_at=now,
            )
        )
    # One alarm per chart per run: a persisting drift re-fires within a few posts after the h/2 reset.
    raised: dict[str, Alarm] = {}
    for a in report.alarms:
        chart = DISPLAY.get(a.chart, a.chart)
        if chart in raised:
            continue
        raised[chart] = Alarm(
            workspace_id=ws,
            chart=chart,
            run_id=run.id,
            raised_at=run.cutoff_at,
            direction={"upper": "up", "lower": "down"}.get(a.side or "", a.side),
            stat=a.statistic,
            threshold=a.threshold,
            attribution="unknown",
            detail={"index": a.index, "alarms_this_run": sum(1 for b in report.alarms if b.chart == a.chart)},
            acknowledged_at=None,
            acknowledged_by=None,
            note=None,
        )
        session.add(raised[chart])
    session.flush()
    return {
        "armed": report.armed,
        "new_posts": len(new_pits),
        "alarms": sorted(raised),
        "diagnostics": report.diagnostics is not None,
        "gap_days": gap,
        "notes": list(report.notes),
    }
