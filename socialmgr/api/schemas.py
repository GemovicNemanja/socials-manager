"""Request and response models of the web routes that wrap the agent services (services/agent.py).

The web app and the MCP tools call the same services; these models only add what a screen needs on
top: a typed briefing, step labels and details for the run progress panel, and a classified failure
for the collection error banner.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from socialmgr.core.types import PostType
from socialmgr.db.models import Run
from socialmgr.experiments.specs import DayPlan
from socialmgr.services import agent, planned, runs

# The label set (labelling/taxonomy.py), spelled out so the generated web types carry the choices.
# tests/test_api_actions.py checks they match the taxonomy.
PostFormat = Literal["Text", "Image", "Video", "Text + link", "Thread", "Poll"]
Topic = Literal["ai", "software_development", "science_technology", "career", "personal", "other"]

# Response fields with defaults are always sent, so the generated web types mark them required.
OUT = ConfigDict(json_schema_serialization_defaults_required=True)

# A "running" run whose process isn't this server and that started this long ago was interrupted.
RUN_STALE_AFTER = timedelta(minutes=15)


# ---------------------------------------------------------------- briefing


class ScoredCounts(BaseModel):
    model_config = OUT

    above: int = 0
    as_expected: int = 0
    below: int = 0


class NotablePost(BaseModel):
    post: str
    label: Literal["above", "as_expected", "below"]
    outcome_i48: float
    expected_median: float
    text: str


class NearDuplicate(BaseModel):
    post: str
    matches: str
    similarity: float


class PlannedMatches(BaseModel):
    model_config = OUT

    matched: int = 0
    missed: int = 0


class UpcomingSlot(BaseModel):
    slot: str
    planned_at: datetime


class BriefingSpend(BaseModel):
    run_usd: float
    month_usd: float
    cap_usd: float


class BriefingData(BaseModel):
    """The structured part of a run briefing (services/briefing.py ``build``)."""

    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)

    run_id: int
    cutoff_at: datetime
    gap_since_prev_h: float | None = None
    new_posts: int = 0
    scored: ScoredCounts = Field(default_factory=ScoredCounts)
    notable: list[NotablePost] = Field(default_factory=list)
    late_maturity: int = 0
    regimes_opened: list[str] = Field(default_factory=list)
    near_duplicates: list[NearDuplicate] = Field(default_factory=list)
    planned: PlannedMatches = Field(default_factory=PlannedMatches)
    upcoming: list[UpcomingSlot] = Field(default_factory=list)
    curve_version: str | None = None
    spend: BriefingSpend | None = None
    caveats: list[str] = Field(default_factory=list)


class BriefingOut(agent.AgentResponse):
    run_id: int | None
    lines: list[str]  # the templated summary, one entry per line
    briefing: BriefingData | None


def briefing_out(resp: agent.BriefingResponse) -> BriefingOut:
    data = BriefingData.model_validate(resp.data) if resp.data else None
    fields = resp.model_dump(exclude={"data", "run_id"})
    return BriefingOut(**fields, run_id=resp.run_id, lines=resp.summary.splitlines(), briefing=data)


# ---------------------------------------------------------------- runs


RunFailureKind = Literal["reauth", "x_unavailable", "interrupted", "other"]


class RunFailure(BaseModel):
    model_config = OUT

    run_id: int
    step: str | None
    kind: RunFailureKind
    title: str
    detail: str
    command: str | None = None  # a terminal command that fixes it, e.g. `socialmgr connect`


class RunStatusOut(agent.RunStatusResponse):
    active: bool  # a run is executing now (here or in another process)
    step_summaries: dict[str, str]  # step name -> what it does
    step_details: dict[str, str]  # step name -> a short result of the shown run's finished steps
    failure: RunFailure | None


class RunStartOut(BaseModel):
    briefing: BriefingOut
    status: RunStatusOut


def _money(value: Any) -> str:
    if not isinstance(value, int | float):
        return ""
    return f"${float(value):.3f}" if value < 1 else f"${float(value):.2f}"


def _detail(name: str, result: dict[str, Any]) -> str:
    if name == "collect":
        return f"{result.get('new_posts', 0)} new posts · {_money(result.get('cost_usd', 0))}"
    if name == "regimes":
        opened = result.get("opened") or []
        return f"{len(opened)} regime opened" if opened else "no change"
    if name == "match_planned":
        return f"{result.get('matched', 0)} matched · {result.get('missed', 0)} missed"
    if name == "label":
        n = int(result.get("rule_labelled", 0) or 0)
        dups = len(result.get("near_duplicates") or [])
        return f"{n} posts" + (f" · {dups} near-duplicate" if dups else "")
    if name == "freeze_as_of":
        return f"{result.get('frozen', 0)} unplanned posts"
    if name == "score":
        return f"{result.get('scored', 0)} posts"
    if name == "fit_curve":
        return f"{result.get('n_pairs', 0)} capture pairs"
    if name == "backup":
        return "skipped" if result.get("skipped") else "saved"
    return ""


def step_details(run: Run | None) -> dict[str, str]:
    steps = (run.steps or {}) if run else {}
    out = {}
    for name, step in steps.items():
        if step.get("status") == "done":
            text = _detail(name, dict(step.get("result") or {}))
            if text:
                out[name] = text
    return out


def classify_failure(status: agent.RunStatus, active: bool) -> RunFailure | None:
    """What went wrong with an unfinished run, in words the collection error banner can show."""
    if status.status == "running":
        if active:
            return None
        return RunFailure(
            run_id=status.run_id,
            step=next((s.name for s in status.steps if s.status != "done"), None),
            kind="interrupted",
            title=f"Run {status.run_id} was interrupted",
            detail="It stopped early. Start the run again; it resumes from its last finished step.",
        )
    failed = next((s for s in status.steps if s.status == "failed"), None)
    step = failed.name if failed else None
    error = (failed.error if failed else None) or "unknown error"
    kind_name, _, message = error.partition(": ")
    if kind_name in ("ReauthRequired", "OAuthError"):
        return RunFailure(
            run_id=status.run_id,
            step=step,
            kind="reauth",
            title="X sign-in expired, so this run collected nothing",
            detail=(
                f"{message or error} Reconnect, then start the run again; it resumes from the step that "
                "stopped and nothing already collected is fetched twice."
            ),
            command="socialmgr connect",
        )
    if kind_name in ("XUnavailable", "TokenStoreBusy"):
        return RunFailure(
            run_id=status.run_id,
            step=step,
            kind="x_unavailable",
            title="Could not reach X, so this run stopped",
            detail=f"{message or error} Your stored sign-in is untouched; start the run again in a minute.",
        )
    return RunFailure(
        run_id=status.run_id,
        step=step,
        kind="other",
        title=f"Run {status.run_id} stopped at step “{step}”" if step else f"Run {status.run_id} stopped",
        detail=f"{error}. Start the run again; it resumes from this step.",
    )


def run_status_out(session: Session, resp: agent.RunStatusResponse, running_here: bool) -> RunStatusOut:
    unfinished = resp.unfinished
    active = running_here or (
        unfinished is not None
        and unfinished.status == "running"
        and datetime.now(UTC) - unfinished.started_at < RUN_STALE_AFTER
    )
    shown = unfinished or resp.latest
    return RunStatusOut(
        **resp.model_dump(exclude={"latest", "unfinished"}),
        latest=resp.latest,
        unfinished=unfinished,
        active=active,
        step_summaries={s.name: s.summary for s in runs.STEPS.steps()},
        step_details=step_details(session.get(Run, shown.run_id) if shown else None),
        failure=classify_failure(unfinished, active) if unfinished is not None else None,
    )


# ---------------------------------------------------------------- planned posts


class PlanIn(BaseModel):
    """A post the member intends to publish. ``planned_at`` needs an offset; it is stored in UTC."""

    model_config = ConfigDict(extra="forbid")

    planned_at: AwareDatetime
    text: str | None = Field(default=None, max_length=4000)
    post_type: PostType = "original"
    post_format: PostFormat | None = None
    topic: Topic | None = None
    has_link: bool = False

    def to_request(self) -> planned.PlanRequest:
        labels: dict[str, str] = {}
        if self.post_format:
            labels["format"] = self.post_format
        if self.topic:
            labels["topic"] = self.topic
        if self.has_link:
            labels["has_url"] = "true"
        text = (self.text or "").strip() or None
        return planned.PlanRequest(
            planned_at=self.planned_at.astimezone(UTC),
            text=text,
            post_type=self.post_type,
            labels=labels,
            registered_by="web",
        )


class RegisterIn(PlanIn):
    accept_warnings: bool = False  # register despite spacing warnings (near-duplicates stay refused)


# ---------------------------------------------------------------- experiments (M2)
#
# Typed views of the experiment engine for the Experiments and Account screens. Built by
# services/experiment_views.py; the web app is the owner's surface, so its writes act as the owner.

ExperimentStatus = Literal[
    "draft", "pending_approval", "scheduled", "active", "paused", "concluded", "stopped"
]
Decision = Literal["adopt", "reject", "near_threshold", "continue"]
# MODEL §4.1 against the frozen treatment-blind forecast; "awaiting" = posted, not yet 48h old.
SlotOutcomeStatus = Literal["above", "as_expected", "below", "awaiting", "planned", "missed", "cancelled"]
PerPostLever = Literal[
    "format",
    "media",
    "link_placement",
    "hook",
    "cta",
    "content_kind",
    "topic_ordering",
    "quote",
    "thread_vs_long",
]


class FactorOut(BaseModel):
    name: str
    lever: str
    lever_label: str  # "Link placement"
    control: str
    treatment: str
    applies_to: str  # plain words: "originals and quotes tagged link_post=true"


class NextLookOut(BaseModel):
    look: str  # week-3 | final
    after: datetime  # the decision is read at the first run after this time


class PausedArmOut(BaseModel):
    factor: str
    at: datetime | None
    detail: str


class ExperimentSummaryOut(BaseModel):
    model_config = OUT

    id: str  # exp:3
    key: str
    name: str
    hypothesis: str
    design: Literal["per_post", "switchback"]
    design_label: str  # "Per-post, randomized" / "Switchback, 4-day"
    status: ExperimentStatus
    status_label: str  # Running, Needs approval, Adopt (not confirmed), ...
    status_tone: Literal["default", "warn", "positive", "negative", "accent"]
    factors: list[FactorOut]
    primary_metric: str
    primary_metric_label: str
    delta_ratio: float  # 1.1: the lift the decision rule looks for
    horizon_weeks: float
    weeks_total: int
    week: int | None  # current experiment week, 1-based (None before it starts)
    created_by: str
    created_at: datetime
    started_at: datetime | None
    horizon_end: datetime | None
    concluded_at: datetime | None
    slots_assigned: int
    posts_matured: int  # assigned posts with a 48h outcome
    next_look: NextLookOut | None
    latest_look: str | None
    effect_ratio: float | None  # first factor, latest readout
    effect_interval80: list[float] | None
    effect_text: str  # "1.38× [1.02, 1.86]" or "too early"
    outcome: str | None
    paused_arms: list[PausedArmOut] = Field(default_factory=list)
    pause_reason: str | None = None
    extension_used: bool = False
    extension_offered: bool = False
    pending_approvals: int = 0


class PowerPreviewOut(BaseModel):
    """MODEL §6.8's three numbers at the account's posting rate."""

    posts_per_day: float
    n_posts: int  # per-post: analysed posts; switchback: 4-day blocks
    power_125: float | None
    power_15: float | None
    power_2: float | None
    mde_ratio_80: float | None
    text: str  # "1.25×: 0.14 · 1.5×: 0.38 · 2×: 0.80"
    note: str


class SuggestionCardOut(BaseModel):
    key: str
    name: str
    hypothesis: str
    mechanism: str
    design: Literal["per_post", "switchback"]
    design_label: str
    factor: FactorOut
    primary_metric_label: str
    horizon_weeks: float
    horizon_text: str
    detectability: str
    note: str
    power: PowerPreviewOut
    already_running: bool
    needs_owner_because: list[str]  # MODEL §6.9 items that make even the owner's start a pending approval


class ApprovalCardOut(BaseModel):
    id: str  # approval:4
    approval_id: int
    kind: str
    experiment: str | None
    title: str  # "Start E08: Engager-peak window vs off-peak"
    detail: str  # who asked and why it needs the owner
    reasons: list[str]
    requested_by: str
    requested_at: datetime
    status: Literal["pending", "approved", "rejected"]
    decided_by: str | None
    decided_at: datetime | None
    note: str | None


class ELondStatusOut(BaseModel):
    model_config = OUT

    tests: int = 0
    rejections: int = 0
    alpha: float = 0.10


class ExperimentsOut(agent.AgentResponse):
    running: list[ExperimentSummaryOut]  # active, scheduled, paused
    waiting: list[ExperimentSummaryOut]  # pending approval or draft
    concluded: list[ExperimentSummaryOut]  # concluded or stopped, newest first
    suggestions: list[SuggestionCardOut]
    approvals: list[ApprovalCardOut]  # pending
    elond: ELondStatusOut
    posts_per_week: float  # recent posting rate, for capacity
    tz: str


class ArmOut(BaseModel):
    experiment: str
    factor: str
    lever: str
    arm: Literal["treatment", "control"]
    value: str
    instruction: str  # "put the link in the first self-reply, not in the post"
    measured: bool
    reverted: bool  # drawn treated but reverted to control (guardrail pause or stop)


class SlotRowOut(BaseModel):
    slot: str  # slot:12
    planned_at: datetime
    planned_local: str  # "2026-11-26 07:00" in the account zone
    post_type: str
    labels: dict[str, str]
    state: Literal["planned", "posted", "missed", "cancelled"]
    experiment_slot: bool  # False: a planned post registered outside any experiment
    text: str | None
    arms: list[ArmOut]
    instructions: str  # everything to do for this slot, in plain words
    forecast: str | None  # forecast:88 (treatment-blind for experiment slots)
    forecast_kind: str | None
    forecast_q10: float | None
    forecast_q50: float | None
    forecast_q90: float | None
    forecast_frozen_at: datetime | None
    matched_post: str | None
    outcome_i48: float | None
    pit: float | None
    outcome_status: SlotOutcomeStatus


class FactorReadoutOut(BaseModel):
    factor: str
    lever: str
    control: str
    treatment: str
    decision: Decision
    label: str  # monitor.label: "adopt (confirmed)" only after an e-LOND rejection
    headline: str  # MODEL §6.3 in words: "Keep going", "Adopt (not confirmed)", ...
    reason: str
    decided_at_look: str | None
    effect_ratio: float | None
    interval80: list[float] | None
    p_gt_delta: float | None  # P(lift > 1.1×)
    p_lt_zero: float | None  # P(effect < 0)
    expected_loss: float | None
    n_treated: int
    n_control: int
    randomization_p: float | None
    disagrees_with_randomization_test: bool
    confirmed: bool
    elond: str  # "confirmed (e-LOND)" / "adopt (not confirmed)" / "enters e-LOND when it concludes"
    e_value: float | None


class HealthCheckOut(BaseModel):
    name: str
    ok: bool
    detail: str


class ReadoutViewOut(BaseModel):
    id: str
    look: str
    experiment_week: int
    final: bool
    decision: str
    created_at: datetime
    protocol_version: int
    factors: list[FactorReadoutOut]
    health: list[HealthCheckOut]
    health_ok: bool


class ArmOutcomeCountsOut(BaseModel):
    model_config = OUT

    above: int = 0
    as_expected: int = 0
    below: int = 0
    awaiting: int = 0
    lost: int = 0  # missed or deleted


class FactorPastOut(BaseModel):
    """How the factor's posts landed against their frozen treatment-blind forecasts (plan §6)."""

    factor: str
    treatment: str
    control: str
    treated: ArmOutcomeCountsOut
    controls: ArmOutcomeCountsOut


class ProtocolVersionOut(BaseModel):
    version: int
    hash: str
    created_by: str
    created_at: datetime
    reason: str


class DeviationRowOut(BaseModel):
    slot: str | None
    factor: str
    kind: str
    detail: str
    created_at: datetime


class ELondEntryOut(BaseModel):
    factor: str
    t: int
    e_value: float
    alpha_t: float
    rejected: bool


class ExperimentDetailOut(agent.AgentResponse):
    experiment: ExperimentSummaryOut | None
    tz: str
    readout: ReadoutViewOut | None
    readouts: list[ReadoutViewOut]  # every look, oldest first
    slots: list[SlotRowOut]  # every assigned slot, oldest first
    past: list[FactorPastOut]
    approvals: list[ApprovalCardOut]
    deviations: list[DeviationRowOut]
    elond: list[ELondEntryOut]
    protocol_versions: list[ProtocolVersionOut]
    seed: int | None
    frozen_forecaster: str | None  # the protocol's frozen model version (blind forecasts use it)


class CustomSpecIn(BaseModel):
    """A one-factor per-post experiment written by the owner."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=1000)
    lever: PerPostLever
    control: str = Field(min_length=1, max_length=64)
    treatment: str = Field(min_length=1, max_length=64)
    horizon_weeks: float = Field(default=4.0, gt=0, le=52)


class ProposeIn(BaseModel):
    """Start one of E01-E09 (``suggestion``) or a custom experiment (``custom``), as the owner."""

    model_config = ConfigDict(extra="forbid")

    suggestion: str | None = None
    custom: CustomSpecIn | None = None


class ExperimentActionOut(BaseModel):
    done: bool
    summary: str
    experiment: ExperimentSummaryOut | None
    approval: ApprovalCardOut | None


class StopIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class RegisterSlotsIn(BaseModel):
    """Whole local days, each with its slot times (local, the account's zone) and optional labels
    such as ``content_kind=demo`` or ``link_post=true`` that factors filter on."""

    model_config = ConfigDict(extra="forbid")

    days: list[DayPlan] = Field(min_length=1, max_length=8)


class RegisterSlotsOut(BaseModel):
    registered: bool
    summary: str
    experiments: list[str]
    warnings: list[str]
    slots: list[SlotRowOut]


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class DecideOut(BaseModel):
    done: bool
    summary: str
    approval: ApprovalCardOut | None


class NextPostsOut(BaseModel):
    tz: str
    days: int
    slots: list[SlotRowOut]  # registered in the next ``days`` days (experiment slots and planned posts)
    drawing: list[str]  # experiments new slots would draw arms for
    registered_days: list[str]  # local dates (YYYY-MM-DD) that already have experiment slots
