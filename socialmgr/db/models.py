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


APPEND_ONLY_TABLES = (
    "metric_snapshots",
    "forecasts",
    "forecast_draws",
    "scores",
    # Experiments (MODEL §6): the protocol history, the drawn arms and the e-LOND stream.
    "protocol_versions",
    "assignments",
    "elond_stream",
)


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
    # Text and cadence features known before posting (MODEL §1.4), filled by the label step.
    minhash: Mapped[bytes | None] = mapped_column(LargeBinary)
    text_len_weighted: Mapped[int | None] = mapped_column(Integer)
    newlines: Mapped[int | None] = mapped_column(Integer)
    gap_since_prev_min: Mapped[float | None] = mapped_column(Float)
    own_posts_48h: Mapped[int | None] = mapped_column(Integer)
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("planned_slots.id", name="fk_posts_slot_id"))


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


class PlannedSlot(WorkspaceMixin, Base):
    """A post registered before it's published; its live forecast is frozen at registration."""

    __tablename__ = "planned_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    planned_at: Mapped[datetime]
    text: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    minhash: Mapped[bytes | None] = mapped_column(LargeBinary)
    post_type: Mapped[str] = mapped_column(String(16), default="original")
    labels: Mapped[dict[str, Any]] = mapped_column(default=dict)
    lever_context: Mapped[dict[str, Any] | None]
    experiment_id: Mapped[int | None] = mapped_column(Integer)
    arm: Mapped[str | None] = mapped_column(String(32))
    matched_post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"))
    state: Mapped[str] = mapped_column(String(16))  # planned | posted | missed | cancelled
    registered_by: Mapped[str] = mapped_column(String(16))  # cli | agent | web
    created_at: Mapped[datetime]


class Regime(WorkspaceMixin, Base):
    """Ranker regimes (MODEL §2e): seeded dates plus changes found in ``param.rs``."""

    __tablename__ = "regimes"
    __table_args__ = (UniqueConstraint("workspace_id", "platform", "started_at", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime]
    kind: Mapped[str] = mapped_column(String(16))  # weight | cold_start | source | model | safety | unknown
    source: Mapped[str] = mapped_column(String(16))  # seed | param_rs
    confidence: Mapped[str] = mapped_column(String(8))  # high | low
    ref: Mapped[str | None] = mapped_column(String(64))  # commit sha
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]


class TrackerState(WorkspaceMixin, Base):
    """Last seen version of each external file the run tracks (``param.rs``)."""

    __tablename__ = "tracker_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    ref: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime]


class MaturityCurve(WorkspaceMixin, Base):
    """A fitted maturity curve; forecasts name the version they used (MODEL §1.3)."""

    __tablename__ = "maturity_curves"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    family: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]]
    n_pairs: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    fitted_at: Mapped[datetime]


class ResourceRead(WorkspaceMixin, Base):
    """One billable resource per provider per UTC day: re-reads that day aren't billed again."""

    __tablename__ = "resource_reads"
    __table_args__ = (UniqueConstraint("provider", "resource", "utc_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(128))
    utc_day: Mapped[str] = mapped_column(String(10))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))


class Briefing(WorkspaceMixin, Base):
    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), unique=True)
    created_at: Mapped[datetime]
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]]


class FollowerId(WorkspaceMixin, Base):
    """Followers seen so far, so a run pages the newest-first list only until a known one (§5)."""

    __tablename__ = "follower_ids"
    __table_args__ = (UniqueConstraint("account_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    first_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    first_seen_at: Mapped[datetime]


# ---------------------------------------------------------------- experiments (MODEL §6)


class Experiment(WorkspaceMixin, Base):
    """A randomized experiment. The protocol's frozen fields (metric, model, δ) change only through
    an approved amendment; every change writes a ``protocol_versions`` row."""

    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))  # E01..E09 or a custom key
    suggestion_key: Mapped[str | None] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128))
    hypothesis: Mapped[str] = mapped_column(Text)
    design: Mapped[str] = mapped_column(String(16))  # per_post | switchback
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    primary_metric: Mapped[str] = mapped_column(String(64))
    delta: Mapped[float] = mapped_column(Float)
    horizon_weeks: Mapped[float] = mapped_column(Float)
    # draft | pending_approval | scheduled | active | paused | concluded | stopped
    status: Mapped[str] = mapped_column(String(24), index=True)
    protocol_version: Mapped[int] = mapped_column(Integer, default=1)
    model_plugin: Mapped[str] = mapped_column(String(128))  # registry name of the frozen model
    model_version: Mapped[str] = mapped_column(String(128))  # family@version of the frozen model
    seed: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(16))  # agent | owner
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    scheduled_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]  # local midnight of the first registered day, in UTC
    concluded_at: Mapped[datetime | None]
    extension_used: Mapped[bool] = mapped_column(Boolean, default=False)
    extension_offered_at: Mapped[datetime | None]
    paused_arms: Mapped[dict[str, Any]] = mapped_column(default=dict)  # factor -> {at, bound}
    pause_reason: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[dict[str, Any]] = mapped_column(default=dict)  # factor -> terminal decision
    outcome: Mapped[str | None] = mapped_column(Text)  # templated conclusion


class ProtocolVersion(WorkspaceMixin, Base):
    """Append-only history of an experiment's protocol."""

    __tablename__ = "protocol_versions"
    __table_args__ = (UniqueConstraint("experiment_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[dict[str, Any]]
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    created_by: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)


class Assignment(WorkspaceMixin, Base):
    """Append-only: one drawn arm per (experiment, slot, factor), after the slot's blind forecast."""

    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("experiment_id", "slot_id", "factor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("planned_slots.id"), index=True)
    factor: Mapped[str] = mapped_column(String(64))
    arm: Mapped[int] = mapped_column(Integer)  # 0 control, 1 treatment
    block_key: Mapped[str] = mapped_column(String(64))
    slot_key: Mapped[str] = mapped_column(String(32))  # local planned time, the seeded draw's key
    measured: Mapped[bool] = mapped_column(Boolean, default=True)  # false on switchback washout days
    protocol_version: Mapped[int] = mapped_column(Integer)
    drawn_at: Mapped[datetime]
    blind_forecast_id: Mapped[int] = mapped_column(ForeignKey("forecasts.id"))
    blind_manifest_hash: Mapped[str] = mapped_column(String(64))
    propensity: Mapped[float] = mapped_column(Float, default=0.5)


class Deviation(WorkspaceMixin, Base):
    """Departures from the drawn design: a slot reverted after a pause, a missed slot, a deleted
    post, a post excluded by the owner. Readouts stay intention-to-treat."""

    __tablename__ = "deviations"
    __table_args__ = (UniqueConstraint("experiment_id", "slot_id", "factor", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("planned_slots.id"))
    factor: Mapped[str] = mapped_column(String(64), default="")  # "" when it concerns every factor
    kind: Mapped[str] = mapped_column(
        String(32)
    )  # reverted_after_pause | missed_slot | deleted_post | excluded
    detail: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime]


class Readout(WorkspaceMixin, Base):
    """A look at an experiment: only at the first run after each experiment-week boundary, and
    the final read at horizon + 48h (MODEL §6.3)."""

    __tablename__ = "readouts"
    __table_args__ = (UniqueConstraint("experiment_id", "look"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    look: Mapped[str] = mapped_column(String(16))  # week-1.. | horizon | final
    experiment_week: Mapped[int] = mapped_column(Integer)
    protocol_version: Mapped[int] = mapped_column(Integer)
    factors: Mapped[dict[str, Any]]  # factor -> posterior summary, decision, randomization p
    decision: Mapped[str] = mapped_column(String(24))  # continue | adopt | reject | near_threshold | mixed
    randomization_p: Mapped[dict[str, Any]]  # factor -> p
    health_checks: Mapped[dict[str, Any]]
    final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]


class ELondEntry(WorkspaceMixin, Base):
    """Append-only e-LOND stream over every concluded confirmatory factor (MODEL §6.7)."""

    __tablename__ = "elond_stream"
    __table_args__ = (UniqueConstraint("workspace_id", "t"), UniqueConstraint("experiment_id", "factor"))

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    factor: Mapped[str] = mapped_column(String(64))
    t: Mapped[int] = mapped_column(Integer)
    e_value: Mapped[float] = mapped_column(Float)
    e_kind: Mapped[str] = mapped_column(String(16))  # msprt | p_calibrator (MODEL §6.7)
    gamma_sequence: Mapped[str] = mapped_column(String(32))  # fixed for the whole stream
    alpha: Mapped[float] = mapped_column(Float)
    alpha_t: Mapped[float] = mapped_column(Float)
    rejected: Mapped[bool] = mapped_column(Boolean)
    randomization_p: Mapped[float] = mapped_column(Float)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime]


class Approval(WorkspaceMixin, Base):
    """An item MODEL §6.9 reserves for the owner. Decided only in the web app or the CLI."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"), index=True)
    # start_experiment | amend_protocol | guardrail_override | exclude_posts
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]]
    reasons: Mapped[list[str]] = mapped_column(JSON)
    requested_by: Mapped[str] = mapped_column(String(16))  # agent | owner
    requested_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16), index=True)  # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(Text)
