"""Post detail read model: the Posts screen drawer and (from M1) the ``get_post`` MCP tool."""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Forecast, Label, MetricSnapshot, Post, ScoreRow
from socialmgr.modelling import maturity, scoring
from socialmgr.services.pipeline import owner_account
from socialmgr.services.read_models import Envelope, ForecastKind, PostItem, list_posts


class PostLabel(BaseModel):
    field: str
    value: str
    source: str
    confidence: float | None
    label_set_version: str


class Quantiles(BaseModel):
    q05: float | None
    q10: float | None
    q25: float | None
    q50: float | None
    q75: float | None
    q90: float | None
    q95: float | None


class CurvePoint(BaseModel):
    """The frozen 48h forecast mapped to an earlier or later age with the forecast's maturity curve."""

    age_h: float
    q10: float
    q50: float
    q90: float


class ForecastDetail(BaseModel):
    id: str
    kind: ForecastKind
    metric: str
    horizon_h: float
    model_version: str
    curve_version: str
    quantiles: Quantiles
    frozen_at: datetime
    manifest_hash: str
    draws_hash: str
    snapshot_run_id: str | None
    by_age: list[CurvePoint]


class ScoreDetail(BaseModel):
    outcome: float
    capture_age_h: float
    exact: bool
    late_maturity: bool
    pit_mid: float
    crps_log: float
    in_50: bool
    in_80: bool
    label: scoring.SurpriseLabel
    scored_at: datetime


class SnapshotPoint(BaseModel):
    run_id: str
    fetched_at: datetime
    age_h: float
    provider: str
    impressions_source: str
    impressions: int | None
    likes: int | None
    reposts: int | None
    quotes: int | None
    replies: int | None
    bookmarks: int | None
    profile_clicks: int | None
    link_clicks: int | None


class PostDetail(Envelope):
    post: PostItem
    has_url: bool
    media_kind: str | None
    followers_at_post: int | None
    labels: list[PostLabel]
    forecast: ForecastDetail | None
    score: ScoreDetail | None
    snapshots: list[SnapshotPoint]


# Ages at which the curve is drawn: 1h to 7 days, matching the drawer's axis.
CURVE_AGES_H = (0.25, 0.5, 1, 2, 3, 4, 6, 9, 12, 18, 24, 30, 36, 42, 48, 60, 72, 96, 120, 144, 168)


def _by_age(fc: Forecast) -> list[CurvePoint]:
    """Only for curves this code knows; a fitted curve (M1) will bring its own G(a)."""
    if fc.curve_version != maturity.PRIOR_CURVE_VERSION:
        return []
    q = fc.quantiles
    return [
        CurvePoint(
            age_h=age,
            q10=round(q["q10"] * maturity.prior_curve(age), 1),
            q50=round(q["q50"] * maturity.prior_curve(age), 1),
            q90=round(q["q90"] * maturity.prior_curve(age), 1),
        )
        for age in CURVE_AGES_H
    ]


def _quantile(q: dict[str, float], key: str) -> float | None:
    value = q.get(key)
    return round(float(value), 1) if value is not None else None


def post_detail(session: Session, settings: Settings, post_id: int) -> PostDetail | None:
    ws = settings.workspace_id
    owner = owner_account(session, ws)
    post = session.get(Post, post_id)
    if owner is None or post is None or post.account_id != owner.id or post.deleted_at is not None:
        return None
    page = list_posts(session, settings, limit=1_000_000)
    item = next((i for i in page.items if i.id == f"post:{post.id}"), None)
    if item is None:
        return None

    latest_labels: dict[str, Label] = {}
    for label in session.scalars(select(Label).where(Label.post_id == post.id).order_by(Label.created_at)):
        latest_labels[label.field] = label

    fc = session.scalar(
        select(Forecast)
        .where(Forecast.workspace_id == ws, Forecast.subject == f"post:{post.id}")
        .order_by(Forecast.frozen_at)
        .limit(1)
    )
    sc = session.scalar(select(ScoreRow).where(ScoreRow.forecast_id == fc.id)) if fc else None
    snaps = session.scalars(
        select(MetricSnapshot).where(MetricSnapshot.post_id == post.id).order_by(MetricSnapshot.age_s)
    ).all()

    media_kind = post.media.get("kind") if isinstance(post.media, dict) else None
    return PostDetail(
        as_of=page.as_of,
        model_version=fc.model_version if fc else page.model_version,
        data_sufficiency=page.data_sufficiency,
        caveats=page.caveats,
        post=item,
        has_url=post.has_url,
        media_kind=str(media_kind) if media_kind is not None else None,
        followers_at_post=post.followers_at_post,
        labels=[
            PostLabel(
                field=lb.field,
                value=lb.value,
                source=lb.source,
                confidence=lb.confidence,
                label_set_version=lb.label_set_version,
            )
            for lb in latest_labels.values()
        ],
        forecast=ForecastDetail(
            id=f"forecast:{fc.id}",
            kind=fc.kind,
            metric=fc.metric,
            horizon_h=fc.horizon_h,
            model_version=fc.model_version,
            curve_version=fc.curve_version,
            quantiles=Quantiles(
                **{k: _quantile(fc.quantiles, k) for k in ("q05", "q10", "q25", "q50", "q75", "q90", "q95")}
            ),
            frozen_at=fc.frozen_at,
            manifest_hash=fc.manifest_hash,
            draws_hash=fc.draws_hash,
            snapshot_run_id=f"run:{fc.snapshot_run_id}" if fc.snapshot_run_id is not None else None,
            by_age=_by_age(fc),
        )
        if fc
        else None,
        score=ScoreDetail(
            outcome=sc.outcome,
            capture_age_h=sc.capture_age_h,
            exact=sc.exact,
            late_maturity=sc.late_maturity,
            pit_mid=round(sc.pit_mid, 4),
            crps_log=round(sc.crps_log, 4),
            in_50=sc.in_50,
            in_80=sc.in_80,
            label=scoring.surprise_label(sc.pit_mid),
            scored_at=sc.scored_at,
        )
        if sc
        else None,
        snapshots=[
            SnapshotPoint(
                run_id=f"run:{s.run_id}",
                fetched_at=s.fetched_at,
                age_h=round(s.age_s / 3600, 2),
                provider=s.provider,
                impressions_source=s.impressions_source,
                impressions=s.impressions,
                likes=s.likes,
                reposts=s.reposts,
                quotes=s.quotes,
                replies=s.replies,
                bookmarks=s.bookmarks,
                profile_clicks=s.profile_clicks,
                link_clicks=s.link_clicks,
            )
            for s in snaps
        ],
    )
