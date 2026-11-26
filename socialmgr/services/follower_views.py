"""Account screen's follower panels and goals (MODEL §2c, §4.2; plan §6 "Followers and goals").

Actuals come from follower counts and captures per run interval. Expectations come from follower
forecasts frozen at earlier runs, so every figure with a past expectation shows how it landed.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import Settings
from socialmgr.db.models import FollowerForecast, FollowerScore, Goal, GoalStatus, Post, Run
from socialmgr.modelling import scoring
from socialmgr.services import agent, draws, goals, pipeline
from socialmgr.services.follower_data import RunInterval, follower_points, run_intervals
from socialmgr.services.plugins import ActivePlugins

WEEK = timedelta(days=7)


def goal_out(g: Goal) -> h.GoalOut:
    return h.GoalOut(
        id=g.id,
        scope=g.scope,
        metric=g.metric,
        target=g.target,
        target_at=g.target_at,
        created_at=g.created_at,
        created_by=g.created_by,
        superseded_at=g.superseded_at,
    )


def _status_summary(g: Goal, status: str, p_goal: float | None) -> str:
    what = (
        f"{g.target:,.0f} followers by {g.target_at:%b %d}"
        if g.scope == "followers" and g.target_at
        else f"{g.target:,.0f} impressions at 48h per post"
    )
    readable = {"working": "Working", "near": "Near threshold", "not_working": "Not working"}.get(
        status, "No status yet"
    )
    chance = f"; P(goal met) {p_goal:.0%}" if p_goal is not None else ""
    return f"{readable} against {what}{chance}."


def goal_status_out(session: Session, g: Goal) -> h.GoalStatusOut:
    st = session.scalar(
        select(GoalStatus)
        .join(Run, Run.id == GoalStatus.run_id)
        .where(GoalStatus.goal_id == g.id)
        .order_by(Run.cutoff_at.desc(), GoalStatus.id.desc())
        .limit(1)
    )
    if st is None:
        return h.GoalStatusOut(
            goal=goal_out(g),
            status="no_goal",
            reason="awaiting_run",
            p_goal=None,
            p_hi=None,
            p_lo=None,
            since=None,
            previous_status=None,
            median=None,
            q10=None,
            q90=None,
            summary=_status_summary(g, "", None) + " The next run computes its status.",
        )
    d: dict[str, Any] = st.detail or {}
    return h.GoalStatusOut(
        goal=goal_out(g),
        status=st.status,
        reason=st.reason,
        p_goal=d.get("p_goal"),
        p_hi=st.p_hi,
        p_lo=st.p_lo,
        since=st.since,
        previous_status=st.previous_status,
        median=d.get("median"),
        q10=d.get("q10"),
        q90=d.get("q90"),
        summary=_status_summary(g, st.status, d.get("p_goal")),
    )


def goals_overview(session: Session, settings: Settings) -> h.GoalsOut:
    current = goals.current_goals(session, settings.workspace_id)
    statuses = [goal_status_out(session, g) for g in current.values()]
    summary = " ".join(s.summary for s in statuses) if statuses else "No goal set."
    return h.GoalsOut(
        **{**agent._envelope(session, settings, ""), "summary": summary},
        current=statuses,
        history=[goal_out(g) for g in goals.goal_history(session, settings.workspace_id)],
    )


def _sum(intervals: list[RunInterval], attr: str) -> float | None:
    vals = [getattr(i, attr) for i in intervals]
    known = [v for v in vals if v is not None]
    return float(sum(known)) if known else None


def _range(q: dict[str, Any]) -> h.FollowerRange:
    return h.FollowerRange(q10=float(q["q10"]), q50=float(q["q50"]), q90=float(q["q90"]))


def _expected_followers_now(
    session: Session, account_id: int, now_followers: int, now_at: datetime
) -> h.ExpectedVsActual | None:
    """The 7-day follower forecast whose horizon has passed most recently, against today's count."""
    fc = session.scalar(
        select(FollowerForecast)
        .where(
            FollowerForecast.account_id == account_id,
            FollowerForecast.horizon_days == 7,
            FollowerForecast.target_at <= now_at,
        )
        .order_by(FollowerForecast.target_at.desc())
        .limit(1)
    )
    if fc is None:
        return None
    samples = draws.load(session, fc.draws_hash)
    pit = scoring.pit_mid(samples, now_followers)
    return h.ExpectedVsActual(
        expected=_range(fc.quantiles),
        actual=float(now_followers),
        percentile=pit,
        outcome=scoring.surprise_label(pit),
        made_at=fc.frozen_at,
    )


def latest_forecast(session: Session, account_id: int) -> h.FollowerForecastOut | None:
    run_id = session.scalar(
        select(FollowerForecast.run_id)
        .where(FollowerForecast.account_id == account_id)
        .order_by(FollowerForecast.frozen_at.desc(), FollowerForecast.id.desc())
        .limit(1)
    )
    if run_id is None:
        return None
    rows = list(
        session.scalars(
            select(FollowerForecast)
            .where(FollowerForecast.account_id == account_id, FollowerForecast.run_id == run_id)
            .order_by(FollowerForecast.horizon_days)
        )
    )
    longest = rows[-1]
    band = [
        h.FollowerBandPoint(
            at=longest.start_at + timedelta(days=int(p["day"])),
            q10=float(p["q10"]),
            q50=float(p["q50"]),
            q90=float(p["q90"]),
        )
        for p in longest.quantiles.get("band", [])
    ]
    return h.FollowerForecastOut(
        id=longest.id,
        made_at=longest.frozen_at,
        kind=longest.kind,
        model_version=longest.model_version,
        start_followers=longest.start_followers,
        band=band,
        horizons={str(r.horizon_days): _range(r.quantiles) for r in rows},
    )


def past_scores(session: Session, account_id: int, limit: int = 12) -> list[h.FollowerScoreOut]:
    rows = session.execute(
        select(FollowerScore, FollowerForecast)
        .join(FollowerForecast, FollowerForecast.id == FollowerScore.follower_forecast_id)
        .where(FollowerForecast.account_id == account_id)
        .order_by(FollowerForecast.target_at.desc())
        .limit(limit)
    ).all()
    return [
        h.FollowerScoreOut(
            forecast_id=fc.id,
            made_at=fc.frozen_at,
            horizon_days=fc.horizon_days,
            target_at=fc.target_at,
            expected=_range(fc.quantiles),
            actual=sc.actual,
            percentile=sc.pit_mid,
            outcome=sc.outcome,
        )
        for sc, fc in rows
    ]


def followers_view(session: Session, settings: Settings) -> h.FollowersOut:
    ws = settings.workspace_id
    owner = pipeline.owner_account(session, ws)
    env = agent._envelope(session, settings, "")
    empty_tile = h.CountTile(value=None, per_day=None, expected=None, note=None)
    if owner is None:
        return h.FollowersOut(
            **{**env, "summary": "No account connected yet."},
            tiles=h.FollowerTiles(
                followers=empty_tile,
                net_7d=None,
                new_follows_7d=empty_tile,
                unfollows_7d=empty_tile,
                profile_visits_7d=empty_tile,
                visit_to_follow_7d=empty_tile,
                replies_7d=empty_tile,
            ),
            actual=[],
            forecast=None,
            annotations=[],
            funnel=[],
            goal=None,
            past=[],
            scenarios=[],
            model_kind="none",
            intervals_n=0,
        )
    points = follower_points(session, owner.id)
    intervals = run_intervals(session, owner.id)
    now_at = points[-1][1] if points else None
    now_f = points[-1][2] if points else None
    recent = [i for i in intervals if now_at is not None and i.end_at > now_at - WEEK]
    days = sum(i.days for i in recent) or None
    gains = _sum(recent, "gains")
    losses = _sum(recent, "losses")
    visits = _sum(recent, "profile_clicks")
    impressions = _sum(recent, "impressions")
    net = sum(i.net for i in recent) if recent else None
    replies = (
        session.scalar(
            select(func.count(Post.id)).where(
                Post.account_id == owner.id,
                Post.type.in_(("reply", "self_reply")),
                Post.created_at > now_at - WEEK,
            )
        )
        if now_at
        else None
    )

    def per_day(v: float | None) -> float | None:
        return v / days if v is not None and days else None

    fc = latest_forecast(session, owner.id)
    current = goals.current_goals(session, ws).get("followers")
    goal = goal_status_out(session, current) if current else None
    scenarios_raw: list[dict[str, Any]] = []
    if fc is not None:
        row = session.get(FollowerForecast, fc.id)
        scenarios_raw = list(row.quantiles.get("scenarios", [])) if row else []
    share = (gains / visits) if gains is not None and visits else None
    tiles = h.FollowerTiles(
        followers=h.CountTile(
            value=float(now_f) if now_f is not None else None,
            per_day=per_day(float(net)) if net is not None else None,
            expected=(
                _expected_followers_now(session, owner.id, now_f, now_at)
                if now_f is not None and now_at
                else None
            ),
            note=None,
        ),
        net_7d=net,
        new_follows_7d=h.CountTile(
            value=gains,
            per_day=per_day(gains),
            expected=None,
            note=None if gains is not None else "Needs a complete follower list on two runs in a row.",
        ),
        unfollows_7d=h.CountTile(
            value=losses, per_day=per_day(losses), expected=None, note="new follows minus net change"
        ),
        profile_visits_7d=h.CountTile(
            value=visits,
            per_day=per_day(visits),
            expected=None,
            note=f"{visits / impressions:.1%} of impressions" if visits and impressions else None,
        ),
        visit_to_follow_7d=h.CountTile(
            value=share,
            per_day=None,
            expected=None,
            note=f"{gains:,.0f} of {visits:,.0f} visits" if share is not None and visits else None,
        ),
        replies_7d=h.CountTile(
            value=float(replies) if replies is not None else None,
            per_day=per_day(float(replies)) if replies is not None else None,
            expected=None,
            note=None,
        ),
    )
    funnel = [
        h.FunnelStep(
            key="impressions", label="Impressions", value=impressions, note="all surfaces, owner API"
        ),
        h.FunnelStep(
            key="profile_visits",
            label="Profile visits",
            value=visits,
            note=f"{visits / impressions:.1%} of impressions" if visits and impressions else "",
        ),
        h.FunnelStep(
            key="new_follows",
            label="New follows",
            value=gains,
            note=f"{share:.1%} of visits" if share is not None else "",
        ),
        h.FunnelStep(
            key="unfollows", label="Unfollows", value=losses, note="derived: new follows minus net change"
        ),
        h.FunnelStep(
            key="net", label="Net", value=float(net) if net is not None else None, note="last 7 days"
        ),
    ]
    kind = fc.kind if fc else "none"
    summary = (
        f"{now_f:,} followers, {net:+,} in the last 7 days."
        if now_f is not None and net is not None
        else "Follower counts start with the first run."
    )
    if fc is not None and "30" in fc.horizons:
        r = fc.horizons["30"]
        summary += f" Expected in 30 days: {r.q50:,.0f} (80%: {r.q10:,.0f}-{r.q90:,.0f})."
    return h.FollowersOut(
        **{**env, "summary": summary},
        tiles=tiles,
        actual=[h.ChartPoint(at=at, value=float(f)) for _, at, f, _ in points],
        forecast=fc,
        annotations=[],
        funnel=funnel,
        goal=goal,
        past=past_scores(session, owner.id),
        scenarios=[
            h.ScenarioOut(
                label=str(s["label"]),
                posts_per_day=float(s["posts_per_day"]),
                q50_30d=float(s["q50_30d"]),
                p_goal=s.get("p_goal"),
            )
            for s in scenarios_raw
        ],
        model_kind=kind,
        intervals_n=len(intervals),
    )


def set_goal(
    session: Session, settings: Settings, body: h.GoalIn, created_by: str, now: datetime
) -> h.GoalSetOut:
    try:
        g = goals.set_goal(
            session,
            settings,
            goals.GoalRequest(scope=body.scope, target=body.target, target_at=body.target_at, note=body.note),
            created_by=created_by,
            now=now,
        )
    except goals.GoalError as exc:
        return h.GoalSetOut(saved=False, summary=str(exc), goal=None)
    session.commit()
    return h.GoalSetOut(
        saved=True,
        summary=_status_summary(g, "", None).replace("No status yet against", "Goal saved:")
        + " A new period starts now; the next run computes its status.",
        goal=goal_out(g),
    )


def goal_preview(
    session: Session, settings: Settings, plugins: "ActivePlugins", body: h.GoalIn, now: datetime
) -> h.GoalPreviewOut:
    """Where the member would stand today against an unsaved goal (the followers step's model)."""
    from socialmgr.services import follower_step

    return follower_step.preview(session, settings, plugins, body, now)
