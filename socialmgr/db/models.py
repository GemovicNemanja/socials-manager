"""Database schema (IMPLEMENTATION_PLAN.md §4).

Shaped for several accounts, platforms and providers from day one: every table carries
``workspace_id`` and account-level rows carry ``account_id``. Tables listed in
``APPEND_ONLY_TABLES`` reject UPDATE and DELETE with database triggers.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores timezone-aware UTC datetimes; SQLite keeps them as naive ISO text."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; store UTC-aware datetimes only")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


APPEND_ONLY_TABLES = ("metric_snapshots", "forecasts", "forecast_draws", "scores")


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime, dict[str, Any]: JSON}


class WorkspaceMixin:
    workspace_id: Mapped[int] = mapped_column(Integer, default=1, index=True)


class Account(WorkspaceMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("workspace_id", "platform", "handle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(64))
    handle: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))  # owner | peer | competitor
    tz: Mapped[str] = mapped_column(String(64), default="UTC")
    premium: Mapped[bool] = mapped_column(Boolean, default=False)
    follower_band: Mapped[str | None] = mapped_column(String(16))
    tracked_since: Mapped[datetime]
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Credential(WorkspaceMixin, Base):
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    provider: Mapped[str] = mapped_column(String(64))
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None]
    refreshed_at: Mapped[datetime | None]


class Provider(WorkspaceMixin, Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    capabilities: Mapped[dict[str, Any]]
    roles: Mapped[dict[str, Any]]
    pricing_version: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Run(WorkspaceMixin, Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # cli | agent | fixture
    started_at: Mapped[datetime]
    cutoff_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16))  # running | finished | failed
    gap_since_prev_h: Mapped[float | None] = mapped_column(Float)
    steps: Mapped[dict[str, Any]] = mapped_column(default=dict)
    spend_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Post(WorkspaceMixin, Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("account_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    in_reply_to_user_id: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)  # removed on compliance deletion
    text_hash: Mapped[str] = mapped_column(String(64))
    has_url: Mapped[bool] = mapped_column(Boolean, default=False)
    media: Mapped[dict[str, Any] | None]
    followers_at_post: Mapped[int | None] = mapped_column(Integer)
    first_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    deleted_at: Mapped[datetime | None]


class MetricSnapshot(WorkspaceMixin, Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("post_id", "run_id", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    provider: Mapped[str] = mapped_column(String(64))
    impressions_source: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime]
    age_s: Mapped[int] = mapped_column(Integer)
    impressions: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    reposts: Mapped[int | None] = mapped_column(Integer)
    quotes: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)
    bookmarks: Mapped[int | None] = mapped_column(Integer)
    profile_clicks: Mapped[int | None] = mapped_column(Integer)
    link_clicks: Mapped[int | None] = mapped_column(Integer)


class FollowerSnapshot(WorkspaceMixin, Base):
    __tablename__ = "follower_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    fetched_at: Mapped[datetime]
    followers: Mapped[int] = mapped_column(Integer)
    new_followers: Mapped[int | None] = mapped_column(Integer)


class Label(WorkspaceMixin, Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    label_set_version: Mapped[str] = mapped_column(String(32))
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16))  # rule | llm | owner | provider
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime]


class FeatureSet(WorkspaceMixin, Base):
    __tablename__ = "feature_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    definition: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]


class ModelVersion(WorkspaceMixin, Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(128), unique=True)
    family: Mapped[str] = mapped_column(String(64))
    prior_source: Mapped[str] = mapped_column(String(64))
    feature_set_version: Mapped[str] = mapped_column(String(32))
    code_version: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16))  # champion | challenger | retired
    created_at: Mapped[datetime]


class Forecast(WorkspaceMixin, Base):
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)  # "post:<id>" or "slot:<id>"
    kind: Mapped[str] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(128))
    curve_version: Mapped[str] = mapped_column(String(32))
    snapshot_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    metric: Mapped[str] = mapped_column(String(32))
    horizon_h: Mapped[float] = mapped_column(Float)
    quantiles: Mapped[dict[str, Any]]
    p_ge_goal: Mapped[float | None] = mapped_column(Float)
    input_manifest: Mapped[dict[str, Any]]
    manifest_hash: Mapped[str] = mapped_column(String(64))
    draws_hash: Mapped[str] = mapped_column(ForeignKey("forecast_draws.hash"))
    frozen_at: Mapped[datetime]


class ForecastDraws(WorkspaceMixin, Base):
    __tablename__ = "forecast_draws"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    n: Mapped[int] = mapped_column(Integer)
    dtype: Mapped[str] = mapped_column(String(16))
    payload: Mapped[bytes] = mapped_column(LargeBinary)  # zlib-compressed little-endian array


class ScoreRow(WorkspaceMixin, Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("forecast_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    forecast_id: Mapped[int] = mapped_column(ForeignKey("forecasts.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    outcome: Mapped[float] = mapped_column(Float)
    capture_age_h: Mapped[float] = mapped_column(Float)
    curve_version: Mapped[str] = mapped_column(String(32))
    exact: Mapped[bool] = mapped_column(Boolean)
    late_maturity: Mapped[bool] = mapped_column(Boolean)
    pit_mid: Mapped[float] = mapped_column(Float)
    pit_rand: Mapped[float] = mapped_column(Float)
    crps_log: Mapped[float] = mapped_column(Float)
    in_80: Mapped[bool] = mapped_column(Boolean)
    in_50: Mapped[bool] = mapped_column(Boolean)
    scored_at: Mapped[datetime]


class Cost(WorkspaceMixin, Base):
    __tablename__ = "costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    provider: Mapped[str] = mapped_column(String(64))
    item: Mapped[str] = mapped_column(String(64))
    amount_usd: Mapped[float] = mapped_column(Float)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    incurred_at: Mapped[datetime]
