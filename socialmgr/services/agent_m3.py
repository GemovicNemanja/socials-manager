"""MCP responses for M3 (plan §8: Account and goals, Model): the same read models as the web app,
wrapped in the agent envelope with a templated summary."""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import Settings
from socialmgr.journal import entries as je
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
    env = _envelope(session, settings, "")
    all_n = env["data_sufficiency"].owner_n
    summary = (
        f"Champion: {m.card.champion} ({m.card.outcome_model}); stage {m.stage}. "
        f"{m.owner_n} matured originals and quotes (drift monitors arm at {m.owner_terms_at}); "
        f"{all_n} matured posts in all, replies included (the model review counts these)."
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


# ---------------------------------------------------------------- public journal (M4)


class JournalEntryOut(BaseModel):
    week: int
    title: str
    status: str  # draft | published
    published_at: datetime | None
    path: str
    summary: str


class JournalResponse(AgentResponse):
    site_url: str | None
    entries: list[JournalEntryOut]
    published_stats: dict[str, float | int | None]


class JournalDraftResponse(AgentResponse):
    written: bool
    entry: JournalEntryOut | None
    markdown: str | None


def _entry_out(e: je.Entry) -> JournalEntryOut:
    return JournalEntryOut(
        week=e.week,
        title=e.title,
        status="published" if e.published else "draft",
        published_at=e.published_at,
        path=str(e.path),
        summary=e.summary,
    )


def get_journal(session: Session, settings: Settings) -> JournalResponse:
    from socialmgr.journal import snapshot

    rows = je.load_all(settings.journal_dir)
    snap = snapshot.build(session, settings, preview=False)
    published = [e for e in rows if e.published]
    summary = (
        f"{len(published)} published entr{'y' if len(published) == 1 else 'ies'}, "
        f"{len(rows) - len(published)} draft(s). "
        + (
            f"Live at {settings.journal.site_url}."
            if settings.journal.site_url
            else "Not published anywhere yet."
        )
    )
    return JournalResponse(
        **_envelope(session, settings, summary),
        site_url=settings.journal.site_url,
        entries=[_entry_out(e) for e in rows],
        published_stats=snap.stats.model_dump(),
    )


def draft_journal_entry(session: Session, settings: Settings, week: int | None) -> JournalDraftResponse:
    from socialmgr.journal import drafts

    try:
        entry = drafts.draft_week(session, settings, week)
    except je.EntryError as exc:
        return JournalDraftResponse(
            **_envelope(session, settings, str(exc)), written=False, entry=None, markdown=None
        )
    summary = (
        f"Draft for week {entry.week} written to {entry.path}. Fill in the TODOs with the member, then they "
        "publish it themselves (socialmgr journal publish --week N); agents can't publish."
    )
    return JournalDraftResponse(
        **_envelope(session, settings, summary),
        written=True,
        entry=_entry_out(entry),
        markdown=je.render(entry),
    )
