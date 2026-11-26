"""Overview screen read model (IMPLEMENTATION_PLAN.md §1, Overview, M1 part).

Every figure here comes from collected data or frozen forecasts. Areas that need data the
system doesn't produce yet (follower forecast, drift monitors, goals) are listed in
``pending_areas`` so the web app and agents can say so instead of guessing. The briefing and
planned posts have their own routes (``/api/briefing``, ``/api/planned``).
"""

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Cost, FollowerSnapshot, Forecast, MetricSnapshot, Post, Run, ScoreRow
from socialmgr.modelling import scoring
from socialmgr.services import draws
from socialmgr.services.pipeline import FORECAST_TYPES, last_finished_run, owner_account
from socialmgr.services.read_models import (
    PLATFORM_LABELS,
    DataSufficiency,
    Envelope,
    PostItem,
    ShellAccount,
    ShellSpend,
    StatusCounts,
    _initials,
    list_posts,
)

RECENT_POSTS = 9
MEDIAN_WINDOW_POSTS = 21
ALL_POSTS = 1_000_000


class ExpectedRange(BaseModel):
    """The 10/50/90% quantiles of a KPI's predictive distribution and where the actual landed."""

    q10: float
    q50: float
    q90: float
    percentile: float  # mid-rank of the actual value in the predictive draws, 0..1
    # Where the actual landed against the expectation (MODEL §4.1 thresholds: >= 0.8, <= 0.2).
    outcome: scoring.SurpriseLabel
    n_posts: int
    method: str


class Kpi(BaseModel):
    value: float | None
    previous: float | None  # the same figure for the window before
    n_posts: int
    expected: ExpectedRange | None


class FollowersKpi(BaseModel):
    now: int | None
    change_7d: int | None
    change_28d: int | None


class RatioKpi(BaseModel):
    value: float | None
    numerator: float | None
    denominator: float | None


class OverviewTiles(BaseModel):
    followers: FollowersKpi
    impressions_7d: Kpi
    median_i48: Kpi
    profile_visits_7d: Kpi
    profile_visit_share_7d: RatioKpi
    follows_per_1k_7d: RatioKpi
    visit_to_follow_7d: RatioKpi
    posts_7d: Kpi


class FollowerPoint(BaseModel):
    at: datetime
    followers: int


class Calibration(BaseModel):
    """Share of scored posts whose outcome fell inside the frozen 50% and 80% ranges."""

    n: int
    in_50_rate: float | None
    in_80_rate: float | None


class OverviewRun(BaseModel):
    id: str
    status: Literal["finished", "running", "failed"]
    started_at: datetime
    cutoff_at: datetime
    finished_at: datetime | None
    gap_since_prev_h: float | None
    spend_usd: float


PendingArea = Literal["follower_forecast", "model_health", "goals"]


class PendingAreaInfo(BaseModel):
    area: PendingArea
    milestone: str
    reason: str


class Overview(Envelope):
    account: ShellAccount | None
    tracked_since: datetime | None
    days_tracked: int | None
    total_posts: int
    tiles: OverviewTiles
    follower_series: list[FollowerPoint]
    status_7d: StatusCounts
    status_28d: StatusCounts
    calibration: Calibration
    recent_posts: list[PostItem]
    last_run: OverviewRun | None
    spend: ShellSpend
    pending_areas: list[PendingAreaInfo]


# Every area the Overview shows has its data since M3; kept for areas a later milestone adds.
PENDING: list[PendingAreaInfo] = []


def _mid_rank(samples: NDArray[np.float64], value: float) -> float:
    return float(np.mean(samples < value) + 0.5 * np.mean(samples == value))


def _expected(
    session: Session, forecasts: list[Forecast], actual: float, stat: Literal["sum", "median"]
) -> ExpectedRange | None:
    """Predictive distribution of a sum or median over posts, from their frozen draws.

    Draw j of every post is combined into one joint draw, which treats posts as independent.
    """
    if not forecasts:
        return None
    sets = [draws.load(session, fc.draws_hash) for fc in forecasts]
    n = min(s.size for s in sets)
    stacked = np.vstack([s[:n] for s in sets])
    joint = stacked.sum(axis=0) if stat == "sum" else np.median(stacked, axis=0)
    q10, q50, q90 = np.quantile(joint, [0.1, 0.5, 0.9])
    return ExpectedRange(
        q10=round(float(q10), 1),
        q50=round(float(q50), 1),
        q90=round(float(q90), 1),
        percentile=round(_mid_rank(joint, actual), 4),
        outcome=scoring.surprise_label(_mid_rank(joint, actual)),
        n_posts=len(forecasts),
        method=f"{stat} of frozen predictive draws, posts treated as independent",
    )


def _count(items: list[PostItem]) -> StatusCounts:
    counts = StatusCounts()
    for item in items:
        setattr(counts, item.status, getattr(counts, item.status) + 1)
    return counts


def _followers_at(series: list[FollowerPoint], t: datetime) -> int | None:
    """The last follower count fetched at or before ``t``."""
    before = [p.followers for p in series if p.at <= t]
    return before[-1] if before else None


def _latest_snapshots(session: Session, post_ids: list[int]) -> dict[int, MetricSnapshot]:
    if not post_ids:
        return {}
    latest_age = (
        select(MetricSnapshot.post_id, func.max(MetricSnapshot.age_s).label("age_s"))
        .where(MetricSnapshot.post_id.in_(post_ids))
        .group_by(MetricSnapshot.post_id)
        .subquery()
    )
    return {
        snap.post_id: snap
        for snap in session.scalars(
            select(MetricSnapshot).join(
                latest_age,
                (latest_age.c.post_id == MetricSnapshot.post_id)
                & (latest_age.c.age_s == MetricSnapshot.age_s),
            )
        )
    }


def _post_db_id(item: PostItem) -> int:
    return int(item.id.split(":", 1)[1])


def overview(session: Session, settings: Settings, now: datetime | None = None) -> Overview:
    now = now or datetime.now(UTC)
    ws = settings.workspace_id
    owner = owner_account(session, ws)
    last_run = last_finished_run(session, ws)
    latest_run = session.scalar(
        select(Run).where(Run.workspace_id == ws).order_by(Run.started_at.desc()).limit(1)
    )
    as_of = last_run.cutoff_at if last_run else None
    ref = as_of or now

    page = list_posts(session, settings, limit=ALL_POSTS)
    items = page.items  # newest first
    week_ago, two_weeks_ago, four_weeks_ago = (
        ref - timedelta(days=7),
        ref - timedelta(days=14),
        ref - timedelta(days=28),
    )
    last_7d = [i for i in items if week_ago < i.created_at <= ref]
    prior_7d = [i for i in items if two_weeks_ago < i.created_at <= week_ago]
    last_28d = [i for i in items if four_weeks_ago < i.created_at <= ref]

    forecasts: dict[str, Forecast] = {}
    for fc in session.scalars(
        select(Forecast)
        .where(Forecast.workspace_id == ws, Forecast.subject.like("post:%"))
        .order_by(Forecast.frozen_at.desc())
    ):
        forecasts[fc.subject] = fc  # earliest frozen wins: the pre-publication forecast where one exists

    # Impressions, 7d: 48h impressions (estimated under 48h) of posts published in the window.
    with_i48 = [i for i in last_7d if i.i48.value is not None]
    impressions_7d = sum(i.i48.value or 0.0 for i in with_i48) if with_i48 else None
    prior_with_i48 = [i for i in prior_7d if i.i48.value is not None]
    impressions_prior = sum(i.i48.value or 0.0 for i in prior_with_i48) if prior_with_i48 else None
    forecast_items = [i for i in with_i48 if i.id in forecasts]
    expected_7d = (
        _expected(
            session,
            [forecasts[i.id] for i in forecast_items],
            sum(i.i48.value or 0.0 for i in forecast_items),
            "sum",
        )
        if forecast_items
        else None
    )

    # Median 48h impressions of the last 21 matured (scored) posts against their frozen forecasts.
    matured = [i for i in items if i.status in ("above", "as_expected", "below") and i.i48.value is not None]
    window = matured[:MEDIAN_WINDOW_POSTS]
    previous_window = matured[MEDIAN_WINDOW_POSTS : 2 * MEDIAN_WINDOW_POSTS]
    median_now = float(median(i.i48.value or 0.0 for i in window)) if window else None
    median_prev = float(median(i.i48.value or 0.0 for i in previous_window)) if previous_window else None
    median_expected = (
        _expected(session, [forecasts[i.id] for i in window if i.id in forecasts], median_now, "median")
        if median_now is not None and all(i.id in forecasts for i in window)
        else None
    )

    # Profile visits and follower conversion use the latest capture of each post in the window.
    snaps = _latest_snapshots(session, [_post_db_id(i) for i in last_7d + prior_7d])

    def visits(window_items: list[PostItem]) -> float | None:
        values = [snaps[_post_db_id(i)].profile_clicks for i in window_items if _post_db_id(i) in snaps]
        known = [v for v in values if v is not None]
        return float(sum(known)) if known else None

    raw_impressions_values = [snaps[_post_db_id(i)].impressions for i in last_7d if _post_db_id(i) in snaps]
    raw_impressions = [v for v in raw_impressions_values if v is not None]
    raw_impressions_7d = float(sum(raw_impressions)) if raw_impressions else None
    visits_7d = visits(last_7d)

    series: list[FollowerPoint] = []
    if owner is not None:
        series = [
            FollowerPoint(at=fs.fetched_at, followers=fs.followers)
            for fs in session.scalars(
                select(FollowerSnapshot)
                .where(FollowerSnapshot.account_id == owner.id, FollowerSnapshot.fetched_at <= ref)
                .order_by(FollowerSnapshot.fetched_at)
            )
        ]
    followers_now = series[-1].followers if series else None
    f7 = _followers_at(series, week_ago)
    f28 = _followers_at(series, four_weeks_ago)
    change_7d = followers_now - f7 if followers_now is not None and f7 is not None else None
    change_28d = followers_now - f28 if followers_now is not None and f28 is not None else None

    def ratio(num: float | None, den: float | None, scale: float = 1.0) -> RatioKpi:
        value = round(scale * num / den, 4) if num is not None and den else None
        return RatioKpi(value=value, numerator=num, denominator=den)

    status_7d = _count(last_7d)
    tiles = OverviewTiles(
        followers=FollowersKpi(now=followers_now, change_7d=change_7d, change_28d=change_28d),
        impressions_7d=Kpi(
            value=impressions_7d, previous=impressions_prior, n_posts=len(with_i48), expected=expected_7d
        ),
        median_i48=Kpi(value=median_now, previous=median_prev, n_posts=len(window), expected=median_expected),
        profile_visits_7d=Kpi(
            value=visits_7d, previous=visits(prior_7d), n_posts=len(last_7d), expected=None
        ),
        profile_visit_share_7d=ratio(visits_7d, raw_impressions_7d),
        follows_per_1k_7d=ratio(
            float(change_7d) if change_7d is not None else None, raw_impressions_7d, 1000.0
        ),
        visit_to_follow_7d=ratio(float(change_7d) if change_7d is not None else None, visits_7d),
        posts_7d=Kpi(
            value=float(len(last_7d)), previous=float(len(prior_7d)), n_posts=len(last_7d), expected=None
        ),
    )

    scored = session.scalars(
        select(ScoreRow).where(ScoreRow.workspace_id == ws, ScoreRow.late_maturity.is_(False))
    ).all()
    calibration = Calibration(
        n=len(scored),
        in_50_rate=round(sum(s.in_50 for s in scored) / len(scored), 4) if scored else None,
        in_80_rate=round(sum(s.in_80 for s in scored) / len(scored), 4) if scored else None,
    )

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = session.scalar(
        select(func.coalesce(func.sum(Cost.amount_usd), 0.0)).where(
            Cost.workspace_id == ws, Cost.incurred_at >= month_start
        )
    )
    total_posts = (
        session.scalar(
            select(func.count(Post.id)).where(
                Post.account_id == owner.id, Post.deleted_at.is_(None), Post.type.in_(FORECAST_TYPES)
            )
        )
        if owner
        else 0
    ) or 0

    caveats = list(page.caveats)
    caveats.append(
        "Expected ranges combine each post's frozen forecast draws as if posts were independent, "
        "so they are likely narrower than the truth"
    )
    if len(with_i48) > len(forecast_items):
        caveats.append(f"{len(with_i48) - len(forecast_items)} posts in the last 7 days have no forecast")

    return Overview(
        as_of=as_of,
        model_version=page.model_version,
        data_sufficiency=DataSufficiency(
            owner_n=page.data_sufficiency.owner_n,
            reason=page.data_sufficiency.reason if total_posts else "no_posts",
        ),
        caveats=caveats,
        account=ShellAccount(
            id=f"acct:{owner.id}",
            handle=owner.handle,
            initials=_initials(owner.handle),
            platform=owner.platform,
            platform_label=f"{PLATFORM_LABELS.get(owner.platform, owner.platform)} · local install",
            tz=owner.tz,
        )
        if owner
        else None,
        tracked_since=owner.tracked_since if owner else None,
        days_tracked=max(0, (ref - owner.tracked_since).days) if owner else None,
        total_posts=total_posts,
        tiles=tiles,
        follower_series=series,
        status_7d=status_7d,
        status_28d=_count(last_28d),
        calibration=calibration,
        recent_posts=items[:RECENT_POSTS],
        last_run=OverviewRun(
            id=f"run:{latest_run.id}",
            status=latest_run.status,
            started_at=latest_run.started_at,
            cutoff_at=latest_run.cutoff_at,
            finished_at=latest_run.finished_at,
            gap_since_prev_h=latest_run.gap_since_prev_h,
            spend_usd=round(latest_run.spend_usd, 2),
        )
        if latest_run
        else None,
        spend=ShellSpend(
            month_label=now.strftime("%b"),
            spent_usd=round(float(spent or 0.0), 2),
            cap_usd=settings.monthly_cap_usd,
        ),
        pending_areas=PENDING,
    )


OUTCOME_TEXT = {"above": "above expected", "as_expected": "as expected", "below": "below expected"}


def account_scorecard(ov: Overview) -> list[str]:
    """How the account did against its expectations, in plain lines (past results, not forecasts)."""
    lines = []
    t = ov.tiles
    for name, kpi, fmt in (
        ("Impressions, last 7 days", t.impressions_7d, "{:,.0f}"),
        (
            f"Median 48h impressions, last {t.median_i48.n_posts} matured "
            f"post{'' if t.median_i48.n_posts == 1 else 's'}",
            t.median_i48,
            "{:,.0f}",
        ),
    ):
        if kpi.value is None:
            continue
        if kpi.expected is None:
            lines.append(f"{name}: {fmt.format(kpi.value)} (no expected range yet).")
            continue
        e = kpi.expected
        lines.append(
            f"{name}: {fmt.format(kpi.value)}, {OUTCOME_TEXT[e.outcome]} "
            f"(expected {fmt.format(e.q10)}-{fmt.format(e.q90)}, median {fmt.format(e.q50)}; "
            f"p{e.percentile * 100:.0f})."
        )
    s7, s28 = ov.status_7d, ov.status_28d
    lines.append(
        f"Posts that reached 48h: last 7 days {s7.above} above / {s7.as_expected} as expected / "
        f"{s7.below} below; "
        f"last 28 days {s28.above} / {s28.as_expected} / {s28.below}."
    )
    if t.followers.now is not None:
        change = f", {t.followers.change_7d:+,} in 7 days" if t.followers.change_7d is not None else ""
        lines.append(f"Followers: {t.followers.now:,}{change}.")
    return lines
