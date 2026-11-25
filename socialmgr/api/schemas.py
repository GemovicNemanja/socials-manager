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
