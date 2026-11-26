"""The run's nowcast step (MODEL §2b batch mode, §4.1 "provisional").

Each owner post younger than 48h at the run gets a provisional I48 from its capture: plain precision
weighting of its frozen forecast and the capture until 50 owner trajectories exist; then the
regression, while it beats the frozen forecast alone in every age band (the nowcast gate).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Forecast, MetricSnapshot, Nowcast, Post, Run, ScoreRow
from socialmgr.modelling import maturity
from socialmgr.modelling import nowcast as nc
from socialmgr.services import draws, pipeline


def _first_capture_under_48h(
    session: Session, post_ids: list[int], cutoff: datetime
) -> dict[int, MetricSnapshot]:
    """Each post's earliest capture younger than 48h, from runs up to ``cutoff``."""
    rows = session.scalars(
        select(MetricSnapshot)
        .join(Run, Run.id == MetricSnapshot.run_id)
        .where(
            MetricSnapshot.post_id.in_(post_ids),
            MetricSnapshot.age_s < int(maturity.PRIMARY_AGE_H * 3600),
            MetricSnapshot.impressions.is_not(None),
            Run.cutoff_at <= cutoff,
        )
        .order_by(MetricSnapshot.age_s)
    ).all()
    out: dict[int, MetricSnapshot] = {}
    for snap in rows:
        out.setdefault(snap.post_id, snap)
    return out


def trajectories(session: Session, settings: Settings, run: Run) -> list[nc.Trajectory]:
    """Matured owner posts that also had a capture under 48h, with their frozen forecasts."""
    ws = settings.workspace_id
    rows = session.execute(
        select(Post, Forecast, ScoreRow)
        .join(Forecast, Forecast.subject == "post:" + Post.id.cast(Forecast.subject.type))
        .join(ScoreRow, ScoreRow.forecast_id == Forecast.id)
        .join(Run, Run.id == ScoreRow.run_id)
        .where(Forecast.workspace_id == ws, Run.cutoff_at <= run.cutoff_at, ScoreRow.late_maturity.is_(False))
    ).all()
    early = _first_capture_under_48h(session, [p.id for p, _, _ in rows], run.cutoff_at)
    out = []
    for post, fc, sc in rows:
        snap = early.get(post.id)
        if snap is None or snap.impressions is None:
            continue
        out.append(
            nc.Trajectory(
                post_id=f"post:{post.id}",
                age_h=snap.age_s / 3600,
                count=snap.impressions,
                outcome_i48=sc.outcome,
                followers=int(fc.input_manifest["subject"]["followers_at_post"]),
                pre=nc.PreForecast.from_draws(draws.load(session, fc.draws_hash)),
                created_at=post.created_at,
                visible_at=sc.scored_at,
            )
        )
    return sorted(out, key=lambda t: (t.created_at or run.cutoff_at, t.post_id))


GATE_EVERY = 21


def _last_gate(session: Session, ws: int, run: Run) -> dict[str, Any] | None:
    for steps in session.scalars(
        select(Run.steps)
        .where(Run.workspace_id == ws, Run.cutoff_at < run.cutoff_at, Run.status == "finished")
        .order_by(Run.cutoff_at.desc())
        .limit(60)
    ):
        gate = ((steps or {}).get("nowcast") or {}).get("result", {}).get("gate")
        if gate and "n" in gate:
            return dict(gate)
    return None


def run_step(session: Session, settings: Settings, run: Run, now: datetime) -> dict[str, Any]:
    ws = settings.workspace_id
    curve = pipeline.curve_as_of(session, ws, run.cutoff_at)
    trajs = trajectories(session, settings, run)
    regression = None
    gate: dict[str, Any] | None = None
    if len(trajs) >= nc.MIN_TRAJECTORIES:
        # The gate's leave-future-out backtest refits the regression per trajectory: re-run it only
        # every 21 new trajectories (MODEL §4.3's cadence) and reuse the last verdict in between.
        gate = _last_gate(session, ws, run)
        if gate is None or len(trajs) - int(gate["n"]) >= GATE_EVERY:
            result = nc.nowcast_gate(nc.backtest_nowcast(trajs, curve))
            gate = {"passed": result.passed, "detail": result.detail, "n": len(trajs)}
        if gate["passed"]:
            regression = nc.fit_regression(trajs)
    young = session.execute(
        select(Post, MetricSnapshot, Forecast)
        .join(MetricSnapshot, MetricSnapshot.post_id == Post.id)
        .join(Forecast, Forecast.subject == "post:" + Post.id.cast(Forecast.subject.type))
        .where(
            MetricSnapshot.run_id == run.id,
            MetricSnapshot.age_s < int(maturity.PRIMARY_AGE_H * 3600),
            MetricSnapshot.impressions.is_not(None),
            Post.type.in_(pipeline.FORECAST_TYPES),
            Forecast.workspace_id == ws,
        )
    ).all()
    done = set(session.scalars(select(Nowcast.post_id).where(Nowcast.run_id == run.id)))
    made = 0
    for post, snap, fc in young:
        if post.id in done or snap.impressions is None:
            continue
        pre = nc.PreForecast.from_draws(draws.load(session, fc.draws_hash))
        est = nc.nowcast(
            snap.impressions,
            snap.age_s / 3600,
            pre,
            int(fc.input_manifest["subject"]["followers_at_post"]),
            curve,
            regression,
            n_trajectories=len(trajs),
        )
        session.add(
            Nowcast(
                workspace_id=ws,
                post_id=post.id,
                run_id=run.id,
                capture_age_h=round(snap.age_s / 3600, 3),
                method=est.method,
                quantiles=est.quantiles,
                p_gt_median=est.p_above_pre_median,
                median_pre=pre.median,
                created_at=now,
            )
        )
        done.add(post.id)
        made += 1
    session.flush()
    return {
        "nowcasts": made,
        "trajectories": len(trajs),
        "method": "regression" if regression is not None else "precision_weighting",
        "gate": gate,
    }


def latest_nowcasts(session: Session, workspace_id: int) -> dict[int, Nowcast]:
    """Each post's most recent nowcast."""
    out: dict[int, Nowcast] = {}
    for row in session.scalars(
        select(Nowcast).where(Nowcast.workspace_id == workspace_id).order_by(Nowcast.run_id)
    ):
        out[row.post_id] = row
    return out
