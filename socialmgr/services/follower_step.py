"""The run's followers step (MODEL §2c, §4.2; plan §6 "Followers and goals").

At each run: fit the follower model on run intervals, score frozen follower forecasts whose horizon
has passed (so the Account screen shows past growth against what was expected), freeze new 7/30/90
day forecasts from 4,000 Monte Carlo paths, and update each goal's status with hysteresis.
Per-post impressions in the paths come from the champion reach model at the current follower count.
"""

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import Settings
from socialmgr.core.types import ForecastSubject, PriorContext
from socialmgr.db.models import FollowerForecast, FollowerScore, Forecast, Goal, GoalStatus, Post, Run
from socialmgr.modelling import followers as fm
from socialmgr.modelling import goals as gm
from socialmgr.services import draws, goals, pipeline
from socialmgr.services.follower_data import follower_points, run_intervals
from socialmgr.services.model_data import scored_posts
from socialmgr.services.plugins import ActivePlugins

CHECKPOINTS = (7, 30, 90)
CADENCE_DAYS = 14
SAMPLER_POINTS = 200  # predictive I48 per post type, stored as quantiles in the manifest
STATUS_OUT = {
    "working": "working",
    "near_threshold": "near",
    "not_working": "not_working",
    "no_goal": "no_goal",
}


def engine_intervals(
    session: Session, account_id: int, until: datetime | None = None
) -> list[fm.RunInterval]:
    return [
        fm.RunInterval(
            start=i.start_at,
            end=i.end_at,
            followers_start=i.followers_start,
            followers_end=i.followers_end,
            gains=i.gains,
            gains_complete=i.gains is not None,
            impressions=float(i.impressions),
            profile_clicks=float(i.profile_clicks) if i.profile_clicks is not None else None,
        )
        for i in run_intervals(session, account_id, until)
    ]


def _covariate(fit: fm.FollowerFit, interval: fm.RunInterval) -> float | None:
    if fit.level == "L2":
        return interval.profile_clicks
    if fit.level == "L3":
        return None if interval.impressions is None else interval.impressions / 1000
    return None


def follower_pits(session: Session, settings: Settings, run: Run) -> tuple[list[float], bool]:
    """Randomized PIT of the gains of the interval this run closed, under the fit made before it."""
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        return [], False
    intervals = engine_intervals(session, owner.id, run.cutoff_at)
    if len(intervals) < 2 or intervals[-1].end != run.cutoff_at:
        return [], False
    last = intervals[-1]
    fit = fm.fit_follower_model(intervals[:-1])
    if last.gains is None or not last.gains_complete:
        return [], fit.label == "fitted"
    days = (last.end - last.start).total_seconds() / 86400
    pred = fit.one_step_predictive(days, _covariate(fit, last))
    return [pred.randomized_pit(int(last.gains), f"followers:{run.id}")], fit.label == "fitted"


def reach_sampler(
    session: Session, settings: Settings, plugins: ActivePlugins, run: Run, followers: int
) -> fm.ArrayImpressionSampler:
    """Predictive I48 by post type from the champion as of this run, at today's follower count."""
    ws = settings.workspace_id
    curve = pipeline.curve_as_of(session, ws, run.cutoff_at)
    training = pipeline.matured_examples(session, ws, run.cutoff_at, curve)
    model = plugins.model(pipeline.champion_at(session, ws, run.cutoff_at) or plugins.champion_name)
    owner_n = sum(1 for e in training if e.account_role == "owner")
    prior = plugins.prior_source.prior(
        PriorContext(platform=settings.account.platform, followers=followers, owner_matured_posts=owner_n)
    )
    fitted = model.fit(training, prior, seed=run.id)
    probs = (np.arange(SAMPLER_POINTS) + 0.5) / SAMPLER_POINTS
    out: dict[str, tuple[float, ...]] = {}
    for t in pipeline.FORECAST_TYPES:
        subject = ForecastSubject(
            subject_id=f"followers:{run.id}:{t}",
            created_at=run.cutoff_at,
            type=t,
            followers_at_post=followers,
        )
        samples = fitted.predict(subject).samples
        out[t] = tuple(round(float(x), 3) for x in np.quantile(samples, probs))
    return fm.ArrayImpressionSampler(draws=out, followers_ref=followers)


def cadence(session: Session, account_id: int, until: datetime) -> dict[str, float]:
    """Posts per day by type over the last 14 days: the schedule the paths assume."""
    rows = session.execute(
        select(Post.type, func.count(Post.id))
        .where(
            Post.account_id == account_id,
            Post.type.in_(pipeline.FORECAST_TYPES),
            Post.created_at > until - timedelta(days=CADENCE_DAYS),
            Post.created_at <= until,
        )
        .group_by(Post.type)
    ).all()
    return {t: round(n / CADENCE_DAYS, 4) for t, n in rows if n}


def _follower_goal(goal: Goal | None, as_of: datetime) -> fm.FollowerGoal | None:
    if goal is None or goal.target_at is None or goal.target_at <= as_of:
        return None
    days = math.ceil((goal.target_at - as_of).total_seconds() / 86400)
    if days > fm.forecast.MAX_HORIZON_DAYS:
        return None
    return fm.FollowerGoal(target=round(goal.target), horizon_days=days)


def make_forecast(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    run: Run,
    goal: fm.FollowerGoal | None,
    scenarios: bool = True,
) -> tuple[fm.FollowerFit, fm.FollowerForecast] | None:
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        return None
    points = follower_points(session, owner.id, run.cutoff_at)
    if not points:
        return None
    f_now = points[-1][2]
    fit = fm.fit_follower_model(engine_intervals(session, owner.id, run.cutoff_at))
    schedule = cadence(session, owner.id, run.cutoff_at) or {"original": 0.0}
    forecast = fm.forecast_followers(
        fit,
        schedule,
        reach_sampler(session, settings, plugins, run, f_now),
        followers_now=f_now,
        as_of=run.cutoff_at,
        goal=goal,
        horizon_days=max(CHECKPOINTS),
        checkpoints=CHECKPOINTS,
        seed=run.id,
        scenarios=fm.DEFAULT_SCENARIOS if scenarios else (),
        probability_days=(goal.horizon_days,) if goal else (),
    )
    return fit, forecast


def _observed_at(points: list[tuple[int, datetime, int, int | None]], t: datetime) -> int | None:
    """The follower count at ``t``, interpolated between the runs either side (None if none after)."""
    before = [p for p in points if p[1] <= t]
    after = [p for p in points if p[1] >= t]
    if not after:
        return None
    if not before:
        return after[0][2]
    (_, t0, f0, _), (_, t1, f1, _) = before[-1], after[0]
    if t1 == t0:
        return f1
    w = (t - t0).total_seconds() / (t1 - t0).total_seconds()
    return round(f0 + w * (f1 - f0))


def score_due(session: Session, settings: Settings, run: Run, now: datetime) -> list[dict[str, Any]]:
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        return []
    points = follower_points(session, owner.id, run.cutoff_at)
    scored = set(session.scalars(select(FollowerScore.follower_forecast_id)))
    due = session.scalars(
        select(FollowerForecast)
        .where(FollowerForecast.account_id == owner.id, FollowerForecast.target_at <= run.cutoff_at)
        .order_by(FollowerForecast.target_at)
    ).all()
    out = []
    for fc in due:
        if fc.id in scored:
            continue
        observed = _observed_at(points, fc.target_at)
        if observed is None:
            continue
        s = fm.score_follower_forecast(
            draws.load(session, fc.draws_hash).astype(np.int64),
            observed,
            horizon_days=fc.horizon_days,
            followers_at_forecast=fc.start_followers,
        )
        session.add(
            FollowerScore(
                workspace_id=settings.workspace_id,
                follower_forecast_id=fc.id,
                run_id=run.id,
                observed_at=fc.target_at,
                actual=observed,
                pit_mid=s.pit_mid,
                crps_log=s.crps_log,
                outcome=s.outcome,
                scored_at=now,
            )
        )
        out.append(
            {
                "forecast_id": fc.id,
                "horizon_days": fc.horizon_days,
                "made_at": fc.frozen_at.isoformat(),
                "q10": float(fc.quantiles["q10"]),
                "q90": float(fc.quantiles["q90"]),
                "actual": observed,
                "outcome": s.outcome,
            }
        )
    return out


def _freeze(
    session: Session,
    settings: Settings,
    run: Run,
    forecast: fm.FollowerForecast,
    goal_id: int | None,
    now: datetime,
) -> list[FollowerForecast]:
    owner = pipeline.owner_account(session, settings.workspace_id)
    assert owner is not None
    band: list[dict[str, float]] = [
        {
            "day": 0,
            "q10": forecast.followers_now,
            "q50": forecast.followers_now,
            "q90": forecast.followers_now,
        }
    ]
    for d in range(1, max(CHECKPOINTS) + 1):
        q10, q50, q90 = np.quantile(forecast.draws_at(d), [0.1, 0.5, 0.9])
        band.append({"day": d, "q10": float(q10), "q50": float(q50), "q90": float(q90)})
    scenarios = [
        {
            "label": s.name,
            "posts_per_day": float(sum(s.schedule.values())),
            "q50_30d": float(s.quantiles[30]["q50"]) if 30 in s.quantiles else float("nan"),
            "p_goal": s.p_goal,
        }
        for s in forecast.scenarios
        if s.skipped is None
    ]
    manifest = dict(forecast.manifest)
    kind = "calibrated" if forecast.fit_label == "fitted" else "prior_only"
    rows = []
    for d in CHECKPOINTS:
        quantiles: dict[str, Any] = {k: float(v) for k, v in forecast.quantiles[d].items()}
        if d == max(CHECKPOINTS):
            quantiles |= {"band": band, "scenarios": scenarios}
        row = FollowerForecast(
            workspace_id=settings.workspace_id,
            account_id=owner.id,
            run_id=run.id,
            kind=kind,
            model_version=f"{forecast.model_version}+{forecast.version}",
            start_at=forecast.as_of,
            start_followers=forecast.followers_now,
            horizon_days=d,
            target_at=forecast.as_of + timedelta(days=d),
            quantiles=quantiles,
            goal_id=goal_id,
            p_goal=forecast.p_goal,
            input_manifest=manifest if d == max(CHECKPOINTS) else {"see_horizon": max(CHECKPOINTS)},
            manifest_hash=forecast.manifest_hash,
            draws_hash=draws.store(session, forecast.draws_at(d).astype(np.float64), settings.workspace_id),
            frozen_at=now,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def _previous_status(session: Session, goal_id: int, before: datetime) -> gm.GoalStatus | None:
    row = session.scalar(
        select(GoalStatus)
        .join(Run, Run.id == GoalStatus.run_id)
        .where(GoalStatus.goal_id == goal_id, Run.cutoff_at < before)
        .order_by(Run.cutoff_at.desc())
        .limit(1)
    )
    return gm.GoalStatus.from_dict(row.detail["engine"]) if row is not None else None


def _record_status(
    session: Session,
    settings: Settings,
    run: Run,
    goal: Goal,
    st: gm.GoalStatus,
    extra: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    session.add(
        GoalStatus(
            workspace_id=settings.workspace_id,
            goal_id=goal.id,
            run_id=run.id,
            status=STATUS_OUT[st.status],
            reason=st.reason,
            p_hi=st.p_hi,
            p_lo=st.p_lo,
            since=st.since,
            previous_status=STATUS_OUT.get(st.previous_status or "", st.previous_status),
            detail={**extra, "engine": st.to_dict()},
            created_at=now,
        )
    )
    return {
        "scope": goal.scope,
        "status": STATUS_OUT[st.status],
        "previous": STATUS_OUT.get(st.previous_status or "", st.previous_status),
        "p_goal": extra.get("p_goal"),
    }


def posts_goal(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    run: Run,
    goal: Goal,
    previous: gm.GoalStatus | None,
) -> tuple[gm.GoalStatus, dict[str, Any]]:
    """MODEL §4.2 for the last 21 originals: θ_S from the champion's typical original plus the scope effect.

    The base is the log1p-median of the champion's predictive for an original now (a point value);
    the scope effect's posterior, updated by the scope's outcomes against their frozen forecasts,
    carries the uncertainty.
    """
    ws = settings.workspace_id
    recent = scored_posts(session, ws, types=("original",), until=run.cutoff_at)[-gm.SCOPE_SIZE :]
    hashes = {
        fc.id: fc.draws_hash
        for fc in session.scalars(select(Forecast).where(Forecast.id.in_([r.forecast_id for r in recent])))
    }
    scope = [
        gm.ScopePost.from_draws(f"post:{r.post_id}", r.outcome, draws.load(session, hashes[r.forecast_id]))
        for r in recent
    ]
    owner = pipeline.owner_account(session, ws)
    assert owner is not None
    points = follower_points(session, owner.id, run.cutoff_at)
    f_now = points[-1][2] if points else 0
    sampler = reach_sampler(session, settings, plugins, run, f_now)
    base = float(np.log1p(np.median(sampler.draws["original"])))
    st = gm.posts_goal_status(goal.target, [base], scope, as_of=run.cutoff_at, previous=previous)
    extra = {
        "p_goal": None,
        "median": float(np.expm1(st.theta_median)) if st.theta_median is not None else None,
        "q10": float(np.expm1(st.interval80[0])) if st.interval80 else None,
        "q90": float(np.expm1(st.interval80[1])) if st.interval80 else None,
        "n_posts": len(scope),
    }
    return st, extra


def run_step(
    session: Session, settings: Settings, plugins: ActivePlugins, run: Run, now: datetime
) -> dict[str, Any]:
    ws = settings.workspace_id
    owner = pipeline.owner_account(session, ws)
    if owner is None:
        return {"skipped": "no owner account"}
    scored = score_due(session, settings, run, now)
    current = goals.current_goals(session, ws)
    f_goal_row = current.get("followers")
    f_goal = _follower_goal(f_goal_row, run.cutoff_at)
    result: dict[str, Any] = {"scored": scored, "goals": []}
    exists = session.scalar(
        select(FollowerForecast.id).where(
            FollowerForecast.run_id == run.id, FollowerForecast.account_id == owner.id
        )
    )
    made = None if exists else make_forecast(session, settings, plugins, run, f_goal)
    if made is not None:
        fit, forecast = made
        _freeze(session, settings, run, forecast, f_goal_row.id if f_goal_row and f_goal else None, now)
        result |= {"fit": fit.label, "level": fit.level, "paths": forecast.n_paths}
        if f_goal_row is not None and f_goal is not None:
            st = gm.follower_goal_status(
                forecast,
                f_goal,
                as_of=run.cutoff_at,
                previous=_previous_status(session, f_goal_row.id, run.cutoff_at),
            )
            d = forecast.draws_at(f_goal.horizon_days)
            extra = {
                "p_goal": forecast.p_goal,
                "median": float(np.median(d)),
                "q10": float(np.quantile(d, 0.1)),
                "q90": float(np.quantile(d, 0.9)),
                "horizon_days": f_goal.horizon_days,
            }
            result["goals"].append(_record_status(session, settings, run, f_goal_row, st, extra, now))
    posts_row = current.get("posts")
    if posts_row is not None:
        st, extra = posts_goal(
            session, settings, plugins, run, posts_row, _previous_status(session, posts_row.id, run.cutoff_at)
        )
        result["goals"].append(_record_status(session, settings, run, posts_row, st, extra, now))
    session.flush()
    return result


def preview(
    session: Session, settings: Settings, plugins: ActivePlugins, body: h.GoalIn, now: datetime
) -> h.GoalPreviewOut:
    """Where the member would stand at the last run against a goal they haven't saved."""
    run = pipeline.last_finished_run(session, settings.workspace_id)

    def empty(summary: str, reason: str = "awaiting_model") -> h.GoalPreviewOut:
        return h.GoalPreviewOut(
            status="no_goal",
            reason=reason,
            p_goal=None,
            median=None,
            q10=None,
            q90=None,
            paths=0,
            summary=summary,
        )

    if run is None:
        return empty("No run has finished yet.")
    try:
        goals.validate(goals.GoalRequest(scope=body.scope, target=body.target, target_at=body.target_at), now)
    except goals.GoalError as exc:
        return empty(str(exc), "invalid")
    if body.scope == "posts":
        fake = Goal(scope="posts", target=body.target)
        st, extra = posts_goal(session, settings, plugins, run, fake, None)
        return h.GoalPreviewOut(
            status=STATUS_OUT[st.status],
            reason=st.reason,
            p_goal=None,
            median=extra["median"],
            q10=extra["q10"],
            q90=extra["q90"],
            paths=0,
            summary=f"{gm.STATUS_LABELS[st.status]}: a typical original now reaches about "
            f"{extra['median']:,.0f} impressions at 48h (80%: {extra['q10']:,.0f}-{extra['q90']:,.0f}).",
        )
    assert body.target_at is not None
    f_goal = _follower_goal(
        Goal(scope="followers", target=body.target, target_at=body.target_at), run.cutoff_at
    )
    if f_goal is None:
        return empty(
            f"Follower goals reach at most {fm.forecast.MAX_HORIZON_DAYS} days past the last run.", "invalid"
        )
    made = make_forecast(session, settings, plugins, run, f_goal, scenarios=False)
    if made is None:
        return empty("No follower counts yet.")
    _, forecast = made
    st = gm.follower_goal_status(forecast, f_goal, as_of=run.cutoff_at)
    d = forecast.draws_at(f_goal.horizon_days)
    q10, q50, q90 = (float(x) for x in np.quantile(d, [0.1, 0.5, 0.9]))
    return h.GoalPreviewOut(
        status=STATUS_OUT[st.status],
        reason=st.reason,
        p_goal=forecast.p_goal,
        median=q50,
        q10=q10,
        q90=q90,
        paths=forecast.n_paths,
        summary=f"P(goal met) {forecast.p_goal:.0%} from {forecast.n_paths:,} simulated paths; median path "
        f"{q50:,.0f} on {body.target_at:%b %d} (80%: {q10:,.0f}-{q90:,.0f}).",
    )
