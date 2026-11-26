"""MCP responses for M3 (plan §8: Account and goals, Model): the same read models as the web app,
wrapped in the agent envelope with a templated summary."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import Settings
from socialmgr.services import follower_views, model_views
from socialmgr.services.agent import AgentResponse, _envelope


class ModelStatusResponse(AgentResponse):
    stage: h.Stage
    card: h.ModelCardOut
    last_review: h.ReviewOut | None
    reviews: list[h.ReviewOut]


class CalibrationResponse(AgentResponse):
    hit_80: h.HitRateTile
    hit_50: h.HitRateTile
    skill: h.SkillTile
    calibration: h.CalibrationTile
    coverage: list[h.CoverageRow]
    pit: h.PitHistogram
    width_ratio: float | None


class MonitorsResponse(AgentResponse):
    drift: h.DriftTile
    monitors: list[h.MonitorOut]
    open_alarms: list[h.AlarmOut]
    changes: list[h.ChangeRowOut]


class AckResponse(AgentResponse):
    acknowledged: bool
    alarm: h.AlarmOut | None


class FollowerForecastResponse(AgentResponse):
    model_kind: str
    forecast: h.FollowerForecastOut | None
    past: list[h.FollowerScoreOut]  # how earlier follower forecasts landed
    goal: h.GoalStatusOut | None
    scenarios: list[h.ScenarioOut]
    tiles: h.FollowerTiles


class GoalResponse(AgentResponse):
    saved: bool
    goal: h.GoalOut | None
    current: list[h.GoalStatusOut]


def _pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "n/a"


def model_status(session: Session, settings: Settings) -> ModelStatusResponse:
    m = model_views.model_health(session, settings)
    review = m.last_review
    summary = (
        f"Champion: {m.card.champion} ({m.card.outcome_model}); stage {m.stage}, {m.owner_n} matured posts."
    )
    if review is None:
        summary += f" First promotion review at {m.first_review_at} matured posts."
    else:
        failed = [g.label for g in review.gates if g.passed is False]
        summary += f" Last review {review.at:%b %d}: {review.decision} ({review.reason})."
        if failed:
            summary += " Failed gates: " + ", ".join(failed) + "."
    return ModelStatusResponse(
        **_envelope(session, settings, summary),
        stage=m.stage,
        card=m.card,
        last_review=review,
        reviews=m.reviews,
    )


def get_calibration(session: Session, settings: Settings) -> CalibrationResponse:
    m = model_views.model_health(session, settings)
    summary = (
        f"Last {m.hit_80.n} matured originals: 80% intervals held {_pct(m.hit_80.value)} "
        f"(band {_pct(m.hit_80.band_lo)}-{_pct(m.hit_80.band_hi)}), 50% held {_pct(m.hit_50.value)}. "
        f"Berkowitz p {m.calibration.berkowitz_p if m.calibration.berkowitz_p is not None else 'n/a'}."
    )
    if m.skill.value is not None:
        summary += f" CRPS skill vs trailing median {m.skill.value:+.2f} (from the last review's backtest)."
    return CalibrationResponse(
        **_envelope(session, settings, summary),
        hit_80=m.hit_80,
        hit_50=m.hit_50,
        skill=m.skill,
        calibration=m.calibration,
        coverage=m.coverage,
        pit=m.pit,
        width_ratio=m.width_ratio,
    )


def get_monitors(session: Session, settings: Settings) -> MonitorsResponse:
    m = model_views.model_health(session, settings)
    open_alarms = [a for a in m.alarms if a.acknowledged_at is None]
    states = ", ".join(f"{mon.label}: {mon.state}" for mon in m.monitors)
    summary = f"{states}. {len(open_alarms)} open alarm(s)."
    return MonitorsResponse(
        **_envelope(session, settings, summary),
        drift=m.drift,
        monitors=m.monitors,
        open_alarms=open_alarms,
        changes=m.changes,
    )


def acknowledge_alarm(session: Session, settings: Settings, alarm_id: int, note: str | None) -> AckResponse:
    out = model_views.acknowledge(session, settings, alarm_id, note, "agent", datetime.now(UTC))
    return AckResponse(
        **_envelope(session, settings, out.summary), acknowledged=out.acknowledged, alarm=out.alarm
    )


def get_follower_forecast(session: Session, settings: Settings) -> FollowerForecastResponse:
    f = follower_views.followers_view(session, settings)
    caveats = []
    if f.model_kind == "prior_only":
        caveats.append("Follower forecast is prior-only: fewer than 4 weeks of follower counts.")
    if f.past:
        last = f.past[0]
        f_summary = (
            f"{f.summary} Last scored follower forecast ({last.horizon_days}d, made {last.made_at:%b %d}): "
            f"expected {last.expected.q10:,.0f}-{last.expected.q90:,.0f}, "
            f"actual {last.actual:,} ({last.outcome})."
        )
    else:
        f_summary = f.summary
    return FollowerForecastResponse(
        **_envelope(session, settings, f_summary, caveats),
        model_kind=f.model_kind,
        forecast=f.forecast,
        past=f.past,
        goal=f.goal,
        scenarios=f.scenarios,
        tiles=f.tiles,
    )


def set_goal(session: Session, settings: Settings, body: h.GoalIn) -> GoalResponse:
    out = follower_views.set_goal(session, settings, body, "agent", datetime.now(UTC))
    current = follower_views.goals_overview(session, settings).current
    return GoalResponse(
        **_envelope(session, settings, out.summary), saved=out.saved, goal=out.goal, current=current
    )


def model_card_text(session: Session, settings: Settings) -> str:
    m = model_views.model_health(session, settings)
    c = m.card
    lines = [
        f"Model card: {c.champion} ({c.version or 'no forecast yet'})",
        f"Outcome model: {c.outcome_model}",
        f"Stage: {c.stage} ({m.owner_n} matured posts; owner terms from {m.owner_terms_at})",
        f"Prior: {c.prior}",
        f"Maturity curve: {c.maturity_curve}",
        f"Challenger: {c.challenger}",
        f"Promotion rule: {c.promotion_rule}",
        f"Last promotion: {c.last_promotion:%Y-%m-%d}" if c.last_promotion else "Last promotion: none",
        f"Refit: {c.refit}",
    ]
    if c.follower_elasticity:
        e = c.follower_elasticity
        lines.append(f"Follower elasticity beta: {e.q50:.2f} [{e.q10:.2f}, {e.q90:.2f}]")
    if m.last_review:
        lines.append("Release gates at the last review:")
        lines += [
            f"  {'pass' if g.passed else 'fail' if g.passed is False else 'n/a'}  {g.label}: {g.detail}"
            for g in m.last_review.gates
        ]
    return "\n".join(lines)
