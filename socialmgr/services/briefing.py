"""The run briefing: what changed since the last run, in a few templated lines (MODEL.md §8)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Briefing, Forecast, PlannedSlot, Post, Run, ScoreRow
from socialmgr.modelling import scoring
from socialmgr.services import pipeline

LABEL_TEXT = {"above": "above its range", "below": "below its range", "as_expected": "as expected"}


def _snippet(text: str | None, n: int = 70) -> str:
    if not text:
        return "(text removed)"
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build(session: Session, settings: Settings, run: Run) -> dict[str, Any]:
    steps = run.steps or {}

    def result(name: str) -> dict[str, Any]:
        return dict(steps.get(name, {}).get("result", {}))

    collect = result("collect")
    scored_rows = session.execute(
        select(ScoreRow, Forecast)
        .join(Forecast, Forecast.id == ScoreRow.forecast_id)
        .where(ScoreRow.run_id == run.id)
    ).all()
    notable = []
    counts = {"above": 0, "as_expected": 0, "below": 0}
    for score, forecast in scored_rows:
        if not forecast.subject.startswith("post:"):
            continue
        label = scoring.surprise_label(score.pit_mid)
        counts[label] += 1
        if label != "as_expected":
            post = session.get(Post, int(forecast.subject.split(":")[1]))
            notable.append(
                {
                    "post": forecast.subject,
                    "label": label,
                    "outcome_i48": round(score.outcome),
                    "expected_median": round(float(forecast.quantiles.get("q50", 0))),
                    "text": _snippet(post.text if post else None),
                }
            )
    caveats: list[str] = []
    for name in ("collect", "regimes", "label", "experiments"):
        caveats += result(name).get("caveats", [])
    previous = pipeline.last_finished_run(session, settings.workspace_id, before=run.cutoff_at)
    if previous is not None:
        for late_step in ("journal", "backup"):  # these run after the briefing
            caveats += (previous.steps or {}).get(late_step, {}).get("result", {}).get("caveats", [])
    from socialmgr.services.overview import account_scorecard, overview

    account = account_scorecard(overview(session, settings, now=run.cutoff_at))
    upcoming = session.scalars(
        select(PlannedSlot)
        .where(PlannedSlot.workspace_id == settings.workspace_id, PlannedSlot.state == "planned")
        .order_by(PlannedSlot.planned_at)
        .limit(5)
    ).all()
    return {
        "run_id": run.id,
        "cutoff_at": run.cutoff_at.isoformat(),
        "gap_since_prev_h": run.gap_since_prev_h,
        "new_posts": collect.get("new_posts", 0),
        "scored": counts,
        "notable": notable,
        "account": account,
        "late_maturity": result("score").get("late_maturity", 0),
        "regimes_opened": result("regimes").get("opened", []),
        "near_duplicates": result("label").get("near_duplicates", []),
        "planned": result("match_planned"),
        "upcoming": [{"slot": f"slot:{s.id}", "planned_at": s.planned_at.isoformat()} for s in upcoming],
        "curve_version": result("fit_curve").get("curve_version"),
        "experiments": result("experiments"),
        "model_review": result("review_model"),
        "monitors": result("monitors"),
        "followers": result("followers"),
        "spend": {
            "run_usd": round(pipeline.run_spend(session, run.id), 4),
            "month_usd": round(pipeline.month_spend(session, settings.workspace_id, run.cutoff_at), 4),
            "cap_usd": settings.monthly_cap_usd,
        },
        "caveats": caveats,
    }


def experiment_lines(exp: dict[str, Any]) -> list[str]:
    """Decisions, pauses and upcoming readouts. "Confirmed" appears only after an e-LOND rejection."""
    lines = []
    for c in exp.get("concluded", []):
        lines.append(f"Experiment {c['experiment']} concluded: {c['outcome']}.")
    for r in exp.get("readouts", []):
        if r["look"] != "final" and not any(
            c["experiment"] == r["experiment"] for c in exp.get("concluded", [])
        ):
            lines.append(
                f"Experiment {r['experiment']} {r['look']} readout: {r['decision'].replace('_', ' ')}."
            )
    for p in exp.get("paused", []):
        what = f"arm {p['factor']}" if p.get("factor") else "the whole experiment"
        lines.append(
            f"Experiment {p['experiment']}: {what} paused ({p['reason'].replace('_', ' ')}, bound "
            f"{p['bound_ratio']:.2f}x); {p['reverted_slots']} planned slot(s) revert to control."
        )
    upcoming = exp.get("upcoming", [])
    if upcoming:
        nxt = ", ".join(f"{u['experiment']} {u['look']} after {u['at'][:10]}" for u in upcoming[:3])
        lines.append(f"Next experiment readouts: {nxt}.")
    return lines


CHART_TEXT = {
    "d1_location": "location drift on 48h impressions",
    "d1_scale": "scale drift on 48h impressions",
    "d1_followers": "follower-gain drift",
    "d3": "adaptive intervals (D3)",
}
STATUS_TEXT = {"working": "Working", "near": "Near threshold", "not_working": "Not working"}


def model_lines(data: dict[str, Any]) -> list[str]:
    """Promotion reviews, drift alarms, how the last follower forecasts landed, and goal status."""
    lines = []
    review = data.get("model_review") or {}
    if review.get("decision") in ("promoted", "held"):
        lines.append(f"Model review: {review['decision']} ({review.get('reason', '')}).")
    for chart in (data.get("monitors") or {}).get("alarms", []):
        lines.append(
            f"Alarm: {CHART_TEXT.get(chart, chart)}. Forecasts may be off; see Forecasts or get_monitors."
        )
    followers = data.get("followers") or {}
    for sc in followers.get("scored", [])[:2]:
        lines.append(
            f"Followers: the {sc['horizon_days']}-day forecast from {sc['made_at'][:10]} expected "
            f"{sc['q10']:,.0f}-{sc['q90']:,.0f}; actual {sc['actual']:,} ({LABEL_TEXT[sc['outcome']]})."
        )
    for g in followers.get("goals", []):
        change = (
            f" (was {STATUS_TEXT.get(g['previous'], g['previous'])})"
            if g.get("previous") and g["previous"] != g["status"]
            else ""
        )
        chance = f", P(goal met) {g['p_goal']:.0%}" if g.get("p_goal") is not None else ""
        lines.append(f"Goal ({g['scope']}): {STATUS_TEXT.get(g['status'], g['status'])}{change}{chance}.")
    return lines


def summarize(data: dict[str, Any]) -> str:
    lines = []
    scored = data["scored"]
    n_scored = sum(scored.values())
    lines.append(
        f"{data['new_posts']} new posts; {n_scored} reached 48h: "
        f"{scored['above']} above range, {scored['as_expected']} as expected, {scored['below']} below."
    )
    lines += [f"Account: {line}" for line in data.get("account", [])[:2]]
    for item in data["notable"][:5]:
        lines.append(
            f"- {item['post']} {LABEL_TEXT[item['label']]}: ~{item['outcome_i48']:,} impressions at 48h "
            f"vs median {item['expected_median']:,}. “{item['text']}”"
        )
    if data["regimes_opened"]:
        lines.append(
            f"X's ranker parameters changed ({', '.join(data['regimes_opened'])}); a new regime is open."
        )
    if data["near_duplicates"]:
        lines.append(f"{len(data['near_duplicates'])} new posts look like near-duplicates of earlier ones.")
    planned = data.get("planned") or {}
    if planned.get("matched") or planned.get("missed"):
        lines.append(
            f"Planned posts: {planned.get('matched', 0)} published, {planned.get('missed', 0)} missed."
        )
    lines += experiment_lines(data.get("experiments") or {})
    lines += model_lines(data)
    spend = data["spend"]
    lines.append(
        f"Spend: ${spend['run_usd']:.3f} this run, "
        f"${spend['month_usd']:.2f} of ${spend['cap_usd']:.2f} this month."
    )
    lines += [f"Note: {c}" for c in data["caveats"]]
    return "\n".join(lines)


def write_briefing(session: Session, settings: Settings, run: Run, now: datetime) -> Briefing:
    data = build(session, settings, run)
    existing = session.scalar(select(Briefing).where(Briefing.run_id == run.id))
    if existing is not None:
        existing.data, existing.summary, existing.created_at = data, summarize(data), now
        session.flush()
        return existing
    row = Briefing(
        workspace_id=settings.workspace_id, run_id=run.id, created_at=now, summary=summarize(data), data=data
    )
    session.add(row)
    session.flush()
    return row


def latest(session: Session, workspace_id: int) -> Briefing | None:
    return session.scalar(
        select(Briefing)
        .where(Briefing.workspace_id == workspace_id)
        .order_by(Briefing.run_id.desc())
        .limit(1)
    )
