"""Forecasts screen read model: model card, release gates, calibration, drift monitors, changes.

Calibration figures come from the frozen as-of forecasts' scores (last 63 originals and quotes,
MODEL §4.3's window). Skill, width and Brier come from the latest promotion review's LFO-CV
backtest (MODEL §5); monitors and alarms from the monitor step's persisted states.
"""

from datetime import datetime
from typing import Any

import numpy as np
from scipy import stats
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import Settings
from socialmgr.db.models import Alarm, Goal, MaturityCurve, ModelReview, MonitorState, Regime, Run
from socialmgr.modelling.monitors import diagnostics, pits_to_z
from socialmgr.services import agent, pipeline
from socialmgr.services.model_data import scored_posts

WINDOW = 63  # MODEL §4.3: diagnostics over the last 63 posts, every 21
CHECK_EVERY = 21
BINS = 10
PIT_EPS = 0.5 / 1001  # PITs come from 1,000 draws (monitors: eps = 0.5/(S+1))

CHART_LABELS = {
    "d1_location": "Location drift, 48h impressions",
    "d1_scale": "Scale drift, 48h impressions",
    "d1_followers": "Follower gains per run",
    "d2": "Goal check (D2)",
    "d3": "Adaptive interval (D3)",
}
MODEL_LABELS = {
    "nb2": "NB2 reach model, priors only",
    "nb2_owner": "NB2 reach model with owner intercepts and dispersion",
    "trailing_median": "Trailing median x follower elasticity",
}


def binomial_band(n: int, p: float) -> tuple[float, float] | None:
    return diagnostics.binomial_band(n, p) if n > 0 else None


def berkowitz_p(pits: list[float]) -> float | None:
    if len(pits) < 10:
        return None
    return diagnostics.berkowitz_test(pits_to_z(pits, PIT_EPS)).p_value


def pit_chi2_p(pits: list[float], bins: int = BINS) -> float | None:
    return diagnostics.pit_uniformity_test(pits, bins).p_value if len(pits) >= 2 * bins else None


def _gate(row: dict[str, Any]) -> h.GateOut:
    return h.GateOut(
        key=str(row.get("key", row.get("name", ""))),
        label=str(row.get("label", row.get("name", ""))),
        passed=row.get("passed"),
        value=row.get("value"),
        threshold=str(row.get("threshold", "")),
        detail=str(row.get("detail", "")),
    )


def review_out(review: ModelReview, at: datetime) -> h.ReviewOut:
    return h.ReviewOut(
        at=at,
        run_id=review.run_id,
        decision=review.decision,
        reason=review.reason,
        candidate=review.candidate_plugin,
        champion_before=review.champion_before,
        champion_after=review.champion_after,
        owner_n=review.owner_n,
        gates=[_gate(g) for g in review.gates],
    )


def alarm_out(a: Alarm) -> h.AlarmOut:
    return h.AlarmOut(
        id=a.id,
        chart=a.chart,
        label=CHART_LABELS.get(a.chart, a.chart),
        raised_at=a.raised_at,
        direction=a.direction,
        stat=a.stat,
        threshold=a.threshold,
        attribution=a.attribution,
        acknowledged_at=a.acknowledged_at,
        note=a.note,
    )


def _reviews(session: Session, ws: int) -> list[tuple[ModelReview, datetime]]:
    return [
        (r, cutoff)
        for r, cutoff in session.execute(
            select(ModelReview, Run.cutoff_at)
            .join(Run, Run.id == ModelReview.run_id)
            .where(ModelReview.workspace_id == ws)
            .order_by(Run.cutoff_at.desc(), ModelReview.id.desc())
        ).all()
    ]


def monitors_out(session: Session, ws: int) -> list[h.MonitorOut]:
    rows = session.execute(
        select(MonitorState, Run.cutoff_at)
        .join(Run, Run.id == MonitorState.run_id)
        .where(MonitorState.workspace_id == ws)
        .order_by(Run.cutoff_at, MonitorState.id)
    ).all()
    by_chart: dict[str, list[tuple[MonitorState, datetime]]] = {}
    for st, at in rows:
        by_chart.setdefault(st.chart, []).append((st, at))
    out = []
    for chart in CHART_LABELS:
        hist = by_chart.get(chart, [])
        if not hist:
            out.append(
                h.MonitorOut(
                    chart=chart,
                    label=CHART_LABELS[chart],
                    armed=False,
                    stat=None,
                    threshold=None,
                    state="off",
                    detail="Switches on at 20 matured posts.",
                    series=[],
                )
            )
            continue
        last, _ = hist[-1]
        state = str(last.state.get("display_state") or ("alarm" if last.alarm else "quiet"))
        out.append(
            h.MonitorOut(
                chart=chart,
                label=CHART_LABELS[chart],
                armed=last.armed,
                stat=last.stat,
                threshold=last.threshold,
                state=state if last.armed else "off",
                detail=str(last.state.get("detail", "")),
                series=[h.ChartPoint(at=at, value=st.stat) for st, at in hist if st.stat is not None],
            )
        )
    return out


def changes_out(session: Session, ws: int, limit: int = 12) -> list[h.ChangeRowOut]:
    rows: list[h.ChangeRowOut] = []
    for reg in session.scalars(
        select(Regime).where(Regime.workspace_id == ws).order_by(Regime.started_at.desc()).limit(limit)
    ):
        rows.append(
            h.ChangeRowOut(
                at=reg.started_at, source="X ranker repo", text=reg.description, action="change-point"
            )
        )
    for review, at in _reviews(session, ws):
        if review.decision in ("promoted", "held"):
            rows.append(
                h.ChangeRowOut(
                    at=at,
                    source="Model",
                    text=(
                        f"{review.champion_after} promoted: {review.reason}"
                        if review.decision == "promoted"
                        else f"{review.candidate_plugin} held: {review.reason}"
                    ),
                    action="refit, PIT reset" if review.decision == "promoted" else "kept previous",
                )
            )
    for goal in session.scalars(select(Goal).where(Goal.workspace_id == ws)):
        what = (
            f"{goal.target:,.0f} followers by {goal.target_at:%b %d}"
            if goal.scope == "followers" and goal.target_at
            else f"{goal.target:,.0f} impressions at 48h per post"
        )
        rows.append(
            h.ChangeRowOut(at=goal.created_at, source="Goal", text=f"Goal set: {what}", action="new period")
        )
    for a in session.scalars(select(Alarm).where(Alarm.workspace_id == ws)):
        rows.append(
            h.ChangeRowOut(
                at=a.raised_at,
                source="Monitor",
                text=f"{CHART_LABELS.get(a.chart, a.chart)} alarm ({a.direction or 'shift'})",
                action="widen intervals, review",
            )
        )
    return sorted(rows, key=lambda r: r.at, reverse=True)[:limit]


def model_health(session: Session, settings: Settings) -> h.ModelHealthOut:
    ws = settings.workspace_id
    scored = scored_posts(session, ws)
    window = scored[-WINDOW:]
    pits = [r.pit_rand for r in window]
    n = len(window)
    owner_n = len(scored)

    def hit(level: float, flags: list[bool]) -> h.HitRateTile:
        band = binomial_band(len(flags), level)
        value = float(np.mean(flags)) if flags else None
        return h.HitRateTile(
            level=level,
            value=value,
            n=len(flags),
            band_lo=band[0] if band else None,
            band_hi=band[1] if band else None,
            in_band=(band[0] <= value <= band[1]) if band and value is not None else None,
        )

    in95 = [0.025 < p < 0.975 for p in pits]
    counts = (
        np.histogram(np.clip(pits, 0, 1 - 1e-12), bins=BINS, range=(0, 1))[0].tolist() if pits else [0] * BINS
    )
    bin_band = (
        (float(stats.binom.ppf(0.05, n, 1 / BINS)), float(stats.binom.ppf(0.95, n, 1 / BINS))) if n else None
    )
    bp, cp = berkowitz_p(pits), pit_chi2_p(pits)

    reviews = _reviews(session, ws)
    last = reviews[0] if reviews else None
    bt: dict[str, Any] = last[0].backtest if last else {}
    ci = bt.get("crps_skill_ci90") or [None, None]
    champion = pipeline.champion_at(session, ws, None) or settings.plugins.champion_model
    latest_review_champion = last[0].champion_after if last else None
    champion = latest_review_champion or champion
    stage: h.Stage = "owner" if champion != "nb2" else "prior_only"

    monitors = monitors_out(session, ws)
    loc = next(m for m in monitors if m.chart == "d1_location")
    alarms = session.scalars(
        select(Alarm).where(Alarm.workspace_id == ws).order_by(Alarm.raised_at.desc()).limit(20)
    ).all()
    loc_alarm = next((a for a in alarms if a.chart == "d1_location"), None)

    curve = session.scalar(select(MaturityCurve).order_by(MaturityCurve.id.desc()).limit(1))
    promoted = next(((r, at) for r, at in reviews if r.decision == "promoted"), None)
    elasticity = bt.get("elasticity")
    env = agent._envelope(session, settings, "")
    summary = (
        f"{n} matured originals in the calibration window; "
        f"80% intervals held {np.mean([r.in_80 for r in window]):.0%} of outcomes."
        if n
        else "No matured originals yet: calibration starts once posts reach 48 hours."
    )
    return h.ModelHealthOut(
        **{**env, "summary": summary},
        stage=stage,
        owner_n=owner_n,
        owner_terms_at=pipeline.OWNER_TERMS_MIN_POSTS,
        hit_80=hit(0.8, [r.in_80 for r in window]),
        hit_50=hit(0.5, [r.in_50 for r in window]),
        skill=h.SkillTile(
            value=bt.get("crps_skill"),
            ci90_lo=ci[0],
            ci90_hi=ci[1],
            passed=(ci[0] > 0) if ci[0] is not None else None,
            n=int(bt.get("n", 0)),
        ),
        calibration=h.CalibrationTile(
            berkowitz_p=bp,
            pit_chi2_p=cp,
            passed=(bp > 0.01 and cp > 0.01) if bp is not None and cp is not None else None,
            n=n,
        ),
        drift=h.DriftTile(
            armed=loc.armed,
            stat=loc.stat,
            threshold=loc.threshold,
            state=loc.state if loc.state != "proposal" else "quiet",
            last_alarm_at=loc_alarm.raised_at if loc_alarm else None,
            reason=loc.detail,
        ),
        pit=h.PitHistogram(
            counts=[int(c) for c in counts],
            n=n,
            band_lo=bin_band[0] if bin_band else None,
            band_hi=bin_band[1] if bin_band else None,
        ),
        coverage=[
            h.CoverageRow(level=0.5, value=float(np.mean([r.in_50 for r in window])) if n else None, n=n),
            h.CoverageRow(level=0.8, value=float(np.mean([r.in_80 for r in window])) if n else None, n=n),
            h.CoverageRow(level=0.95, value=float(np.mean(in95)) if n else None, n=n),
        ],
        width_ratio=bt.get("width_ratio"),
        next_check_in_posts=(CHECK_EVERY - owner_n % CHECK_EVERY) if owner_n else CHECK_EVERY,
        card=h.ModelCardOut(
            champion=champion,
            version=env["model_version"],
            stage=stage,
            outcome_model=MODEL_LABELS.get(champion, champion) + ", 48h impressions",
            prior="MODEL §2a hand-set priors" + (" + owner terms" if stage == "owner" else ""),
            follower_elasticity=h.Elasticity(**elasticity) if elasticity else None,
            post_features=["type", "link"] + (["owner intercepts", "dispersion"] if stage == "owner" else []),
            maturity_curve=f"{curve.version}, fitted on {curve.n_pairs} capture pairs"
            if curve
            else "prior curve",
            challenger=(
                f"{last[0].candidate_plugin} {last[0].decision}: {last[0].reason}"
                if last
                else f"{', '.join(settings.plugins.challenger_models)} (baseline)"
            ),
            promotion_rule="All MODEL §5 release gates pass on the LFO-CV backtest",
            last_promotion=promoted[1] if promoted else None,
            refit="every run",
        ),
        last_review=review_out(*last) if last else None,
        reviews=[review_out(r, at) for r, at in reviews[:10]],
        changes=changes_out(session, ws),
        monitors=monitors,
        alarms=[alarm_out(a) for a in alarms],
    )


def acknowledge(
    session: Session, settings: Settings, alarm_id: int, note: str | None, by: str, now: datetime
) -> h.AckOut:
    from socialmgr.services import alarms

    try:
        a = alarms.acknowledge(session, settings.workspace_id, alarm_id, by, note, now)
    except alarms.AlarmError as exc:
        return h.AckOut(acknowledged=False, summary=str(exc), alarm=None)
    session.commit()
    return h.AckOut(
        acknowledged=True,
        summary=f"alarm:{a.id} acknowledged. Charts, intervals and the model are unchanged.",
        alarm=alarm_out(a),
    )
