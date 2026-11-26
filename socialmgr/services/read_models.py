"""Read models behind the web API and (from M1) the MCP tools. One service, two front doors."""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import (
    Approval,
    Cost,
    Experiment,
    Forecast,
    Label,
    MetricSnapshot,
    Post,
    Run,
    ScoreRow,
)
from socialmgr.modelling import maturity, scoring
from socialmgr.services.pipeline import (
    FORECAST_TYPES,
    OWNER_TERMS_MIN_POSTS,
    curve_as_of,
    last_finished_run,
    owner_account,
    pick_labels,
)

Status = Literal["above", "as_expected", "below", "provisional", "no_forecast"]
StatusFilter = Literal["all", "above", "as_expected", "below", "provisional", "no_forecast"]
ForecastKind = Literal["live", "as_of", "treatment_blind", "prior_only_uncalibrated"]
PostTypeName = Literal["original", "quote", "reply", "repost", "self_reply"]


class DataSufficiency(BaseModel):
    owner_n: int
    reason: str | None


class Envelope(BaseModel):
    as_of: datetime | None
    model_version: str | None
    data_sufficiency: DataSufficiency
    caveats: list[str]


class I48(BaseModel):
    value: float | None
    exact: bool
    provisional: bool
    # Under 48h: the batch nowcast's P(I48 > the frozen forecast's median) and how it was made.
    p_above_median: float | None = None
    method: str | None = None


class ForecastSummary(BaseModel):
    q10: float
    q50: float
    q90: float
    kind: ForecastKind


class PostItem(BaseModel):
    id: str
    external_id: str
    created_at: datetime
    text: str
    type: PostTypeName
    format: str | None
    topic: str | None
    age_h: float
    i48: I48
    likes_per_1k: float | None
    forecast: ForecastSummary | None
    pit_mid: float | None
    status: Status


class StatusCounts(BaseModel):
    above: int = 0
    as_expected: int = 0
    below: int = 0
    provisional: int = 0
    no_forecast: int = 0


class PostsPage(Envelope):
    total: int
    counts_by_status: StatusCounts
    items: list[PostItem]


class ShellAccount(BaseModel):
    id: str
    handle: str
    initials: str
    platform: str
    platform_label: str
    tz: str


class ShellRun(BaseModel):
    id: str
    finished_at: datetime | None
    status: Literal["finished", "running", "failed"]


class ShellSpend(BaseModel):
    month_label: str
    spent_usd: float
    cap_usd: float


class ShellCounts(BaseModel):
    posts: int
    experiments_running: int
    approvals_pending: int


class Shell(BaseModel):
    account: ShellAccount | None
    last_run: ShellRun | None
    spend: ShellSpend
    counts: ShellCounts


PLATFORM_LABELS = {"x": "X"}


def _initials(handle: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", handle) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (parts[0][:2] if parts else "?").upper()


def shell(session: Session, settings: Settings, now: datetime | None = None) -> Shell:
    now = now or datetime.now(UTC)
    owner = owner_account(session, settings.workspace_id)
    latest = session.scalar(
        select(Run).where(Run.workspace_id == settings.workspace_id).order_by(Run.started_at.desc()).limit(1)
    )
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = session.scalar(
        select(func.coalesce(func.sum(Cost.amount_usd), 0.0)).where(
            Cost.workspace_id == settings.workspace_id, Cost.incurred_at >= month_start
        )
    )
    posts = (
        session.scalar(
            select(func.count(Post.id)).where(
                Post.account_id == owner.id, Post.deleted_at.is_(None), Post.type.in_(FORECAST_TYPES)
            )
        )
        if owner
        else 0
    )
    return Shell(
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
        last_run=ShellRun(id=f"run:{latest.id}", finished_at=latest.finished_at, status=latest.status)
        if latest
        else None,
        spend=ShellSpend(
            month_label=now.strftime("%b"),
            spent_usd=round(float(spent or 0.0), 2),
            cap_usd=settings.monthly_cap_usd,
        ),
        counts=ShellCounts(
            posts=posts or 0,
            experiments_running=session.scalar(
                select(func.count(Experiment.id)).where(
                    Experiment.workspace_id == settings.workspace_id,
                    Experiment.status.in_(("active", "scheduled", "paused")),
                )
            )
            or 0,
            approvals_pending=session.scalar(
                select(func.count(Approval.id)).where(
                    Approval.workspace_id == settings.workspace_id, Approval.status == "pending"
                )
            )
            or 0,
        ),
    )


def _status(score: ScoreRow | None, forecast: Forecast | None, age_h: float) -> Status:
    if score is not None:
        return scoring.surprise_label(score.pit_mid)
    if forecast is not None and age_h < maturity.PRIMARY_AGE_H:
        return "provisional"
    return "no_forecast" if forecast is None else "provisional"


def list_posts(
    session: Session, settings: Settings, status: StatusFilter = "all", limit: int = 50, offset: int = 0
) -> PostsPage:
    owner = owner_account(session, settings.workspace_id)
    last_run = last_finished_run(session, settings.workspace_id)
    items: list[PostItem] = []
    counts = StatusCounts()
    owner_n = 0
    model_version: str | None = None
    if owner is not None:
        posts = session.scalars(
            select(Post)
            .where(Post.account_id == owner.id, Post.deleted_at.is_(None), Post.type.in_(FORECAST_TYPES))
            .order_by(Post.created_at.desc())
        ).all()
        forecasts = {
            f.subject: f
            for f in session.scalars(select(Forecast).where(Forecast.workspace_id == settings.workspace_id))
        }
        scores = {
            s.forecast_id: s
            for s in session.scalars(select(ScoreRow).where(ScoreRow.workspace_id == settings.workspace_id))
        }
        latest_age = (
            select(MetricSnapshot.post_id, func.max(MetricSnapshot.age_s).label("age_s"))
            .group_by(MetricSnapshot.post_id)
            .subquery()
        )
        latest = {
            snap.post_id: snap
            for snap in session.scalars(
                select(MetricSnapshot).join(
                    latest_age,
                    (latest_age.c.post_id == MetricSnapshot.post_id)
                    & (latest_age.c.age_s == MetricSnapshot.age_s),
                )
            )
        }
        by_post: dict[int, list[Label]] = {}
        for lb in session.scalars(select(Label)):
            by_post.setdefault(lb.post_id, []).append(lb)
        labels = {pid: {f: lb.value for f, lb in pick_labels(rows).items()} for pid, rows in by_post.items()}
        curve = curve_as_of(session, settings.workspace_id, last_run.cutoff_at if last_run else None)
        from socialmgr.services.nowcast_step import latest_nowcasts

        nowcasts = latest_nowcasts(session, settings.workspace_id)
        owner_n = sum(1 for s in scores.values() if not s.late_maturity)
        for post in posts:
            fc = forecasts.get(f"post:{post.id}")
            sc = scores.get(fc.id) if fc else None
            snap = latest.get(post.id)
            age_h = snap.age_s / 3600 if snap else 0.0
            if sc is not None:
                i48 = I48(value=round(sc.outcome), exact=sc.exact, provisional=False)
            elif post.id in nowcasts:
                n = nowcasts[post.id]
                i48 = I48(
                    value=round(float(n.quantiles["q50"])),
                    exact=False,
                    provisional=True,
                    p_above_median=round(n.p_gt_median, 3),
                    method=n.method,
                )
            elif snap is not None and snap.impressions is not None:
                estimate = curve.estimate_i48(snap.impressions, age_h)
                i48 = I48(
                    value=round(estimate) if estimate is not None else None, exact=False, provisional=True
                )
            else:
                i48 = I48(value=None, exact=False, provisional=True)
            if fc is not None:
                model_version = model_version or fc.model_version
            item_status = _status(sc, fc, age_h)
            setattr(counts, item_status, getattr(counts, item_status) + 1)
            if status != "all" and item_status != status:
                continue
            items.append(
                PostItem(
                    id=f"post:{post.id}",
                    external_id=post.external_id,
                    created_at=post.created_at,
                    text=post.text or "",
                    type=post.type,
                    format=labels.get(post.id, {}).get("format"),
                    topic=labels.get(post.id, {}).get("topic"),
                    age_h=round(age_h, 2),
                    i48=i48,
                    likes_per_1k=round(1000 * snap.likes / snap.impressions, 1)
                    if snap and snap.likes is not None and snap.impressions
                    else None,
                    forecast=ForecastSummary(
                        q10=round(fc.quantiles["q10"]),
                        q50=round(fc.quantiles["q50"]),
                        q90=round(fc.quantiles["q90"]),
                        kind=fc.kind,
                    )
                    if fc
                    else None,
                    pit_mid=round(sc.pit_mid, 4) if sc else None,
                    status=item_status,
                )
            )
    caveats = ["48h values for posts under 48 hours are estimated with the latest fitted maturity curve"]
    reason = None
    if owner_n < OWNER_TERMS_MIN_POSTS:
        reason = "prior_only_uncalibrated"
        caveats.append(
            f"Forecasts are prior-only until {OWNER_TERMS_MIN_POSTS} matured posts (now {owner_n})"
        )
    return PostsPage(
        as_of=last_run.cutoff_at if last_run else None,
        model_version=model_version,
        data_sufficiency=DataSufficiency(owner_n=owner_n, reason=reason),
        caveats=caveats,
        total=len(items),
        counts_by_status=counts,
        items=items[offset : offset + limit],
    )
