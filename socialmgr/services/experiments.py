"""Experiment services for agents (plan §8, M2). The MCP tools and web routes are thin wrappers.

Every response carries the agent envelope (``services/agent.py``) and stable ids (``exp:…``,
``slot:…``, ``forecast:…``, ``readout:…``, ``approval:…``). Every write here acts as
``actor="agent"``: an agent can propose, schedule, register slots, amend, extend and stop, but
MODEL §6.9's owner items only ever become pending approvals. Nothing in this module can decide an
approval; that is ``services/approvals.py``, reachable from the CLI and the web app only.
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import (
    Approval,
    Assignment,
    Deviation,
    ELondEntry,
    Experiment,
    Forecast,
    PlannedSlot,
    ProtocolVersion,
    Readout,
)
from socialmgr.experiments import engine, monitor
from socialmgr.experiments.engine import AmendRequest, ExperimentError
from socialmgr.experiments.specs import Actor, DayPlan, ExperimentSpec
from socialmgr.experiments.suggestions import BY_KEY
from socialmgr.services.agent import AgentResponse, _envelope
from socialmgr.services.plugins import ActivePlugins

ACTOR: Actor = "agent"  # hard-coded: this module never acts as the owner


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC).replace(microsecond=0)


# ---------------------------------------------------------------- output models


class PowerOut(BaseModel):
    design: str
    posts_per_day: float
    n_posts: int
    power: dict[str, float | None]
    mde_ratio_80: float | None
    note: str


class ApprovalOut(BaseModel):
    id: str
    approval_id: int
    kind: str
    experiment: str | None
    payload: dict[str, Any]
    reasons: list[str]
    requested_by: str
    requested_at: datetime
    status: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    how_to_decide: str


class NextLook(BaseModel):
    look: str
    after: datetime


class ExperimentOut(BaseModel):
    id: str
    key: str
    name: str
    hypothesis: str
    design: str
    factors: list[dict[str, Any]]
    primary_metric: str
    secondary_metrics: list[str]
    delta_ratio: float
    horizon_weeks: float
    status: str
    protocol_version: int
    model_version: str
    seed: int
    created_by: str
    created_at: datetime
    started_at: datetime | None
    horizon_end: datetime | None
    extension_used: bool
    extension_offered_at: datetime | None
    paused_arms: dict[str, Any]
    pause_reason: str | None
    decisions: dict[str, Any]
    outcome: str | None
    slots_assigned: int
    next_look: NextLook | None
    pending_approvals: int


class ReadoutOut(BaseModel):
    id: str
    experiment: str
    look: str
    experiment_week: int
    decision: str
    final: bool
    factors: dict[str, Any]
    labels: dict[str, str]  # factor -> "adopt (confirmed)" / "adopt (not confirmed)" / ...
    randomization_p: dict[str, Any]
    health_checks: dict[str, Any]
    protocol_version: int
    run_id: int | None
    created_at: datetime


class SuggestionOut(BaseModel):
    key: str
    name: str
    hypothesis: str
    design: str
    factors: list[dict[str, Any]]
    primary_metric: str
    horizon_weeks: float
    horizon_text: str
    detectability: str
    mechanism: str
    note: str
    power: PowerOut
    already_running: bool


class SlotArmOut(BaseModel):
    experiment: str
    factor: str
    lever: str
    arm: str  # treatment | control
    value: str
    block: str
    measured: bool


class RegisteredSlotOut(BaseModel):
    slot: str
    planned_at: datetime
    planned_local: str
    post_type: str
    labels: dict[str, str]
    state: str
    blind_forecast: str
    blind_manifest_hash: str
    blind_frozen_at: datetime
    blind_q10: float
    blind_q50: float
    blind_q90: float
    arms: list[SlotArmOut]
    instructions: str


class DeviationOut(BaseModel):
    slot: str | None
    factor: str
    kind: str
    detail: str
    created_at: datetime


class ELondOut(BaseModel):
    experiment: str
    factor: str
    t: int
    e_value: float
    alpha_t: float
    rejected: bool
    randomization_p: float


class ProtocolOut(BaseModel):
    version: int
    hash: str
    created_by: str
    created_at: datetime
    reason: str
    protocol: dict[str, Any]


def power_out(p: engine.PowerReport) -> PowerOut:
    return PowerOut(
        design=p.design,
        posts_per_day=p.posts_per_day,
        n_posts=p.n_posts,
        power=p.power,
        mde_ratio_80=p.mde_ratio_80,
        note=p.note,
    )


def approval_out(session: Session, a: Approval) -> ApprovalOut:
    exp = session.get(Experiment, a.experiment_id) if a.experiment_id else None
    return ApprovalOut(
        id=f"approval:{a.id}",
        approval_id=a.id,
        kind=a.kind,
        experiment=exp.key if exp else None,
        payload=a.payload,
        reasons=a.reasons,
        requested_by=a.requested_by,
        requested_at=a.requested_at,
        status=a.status,
        decided_by=a.decided_by,
        decided_at=a.decided_at,
        note=a.note,
        how_to_decide=f"The owner decides on the Experiments screen or with `socialmgr approve {a.id}` "
        f"/ `socialmgr reject {a.id}`.",
    )


def experiment_out(session: Session, exp: Experiment, now: datetime) -> ExperimentOut:
    proto = engine.latest_protocol(session, exp)
    nxt = monitor.next_look(exp, now)
    return ExperimentOut(
        id=f"exp:{exp.id}",
        key=exp.key,
        name=exp.name,
        hypothesis=exp.hypothesis,
        design=exp.design,
        factors=exp.factors,
        primary_metric=exp.primary_metric,
        secondary_metrics=list(proto.protocol.get("secondary_metrics", [])) if proto else [],
        delta_ratio=round(math.exp(exp.delta), 4),
        horizon_weeks=exp.horizon_weeks,
        status=exp.status,
        protocol_version=exp.protocol_version,
        model_version=exp.model_version,
        seed=exp.seed,
        created_by=exp.created_by,
        created_at=exp.created_at,
        started_at=exp.started_at,
        horizon_end=engine.horizon_end(exp),
        extension_used=exp.extension_used,
        extension_offered_at=exp.extension_offered_at,
        paused_arms=exp.paused_arms or {},
        pause_reason=exp.pause_reason,
        decisions=exp.decisions or {},
        outcome=exp.outcome,
        slots_assigned=session.scalar(
            select(func.count(func.distinct(Assignment.slot_id))).where(Assignment.experiment_id == exp.id)
        )
        or 0,
        next_look=NextLook(look=nxt[0], after=nxt[1]) if nxt else None,
        pending_approvals=session.scalar(
            select(func.count(Approval.id)).where(
                Approval.experiment_id == exp.id, Approval.status == "pending"
            )
        )
        or 0,
    )


def readout_out(exp: Experiment, r: Readout) -> ReadoutOut:
    return ReadoutOut(
        id=f"readout:{r.id}",
        experiment=exp.key,
        look=r.look,
        experiment_week=r.experiment_week,
        decision=r.decision,
        final=r.final,
        factors=r.factors,
        labels={k: monitor.label(v) for k, v in r.factors.items()},
        randomization_p=r.randomization_p,
        health_checks=r.health_checks,
        protocol_version=r.protocol_version,
        run_id=r.run_id,
        created_at=r.created_at,
    )


def suggestion_outs(session: Session, settings: Settings, now: datetime) -> list[SuggestionOut]:
    return [
        SuggestionOut(
            key=v.suggestion.key,
            name=v.suggestion.spec.name,
            hypothesis=v.suggestion.spec.hypothesis,
            design=v.suggestion.spec.design,
            factors=[f.model_dump(mode="json") for f in v.suggestion.spec.factors],
            primary_metric=v.suggestion.spec.primary_metric,
            horizon_weeks=v.suggestion.spec.horizon_weeks,
            horizon_text=v.suggestion.horizon_text,
            detectability=f"MODEL §6.10 reference at its planned cadence: {v.suggestion.detectability}",
            mechanism=v.suggestion.mechanism,
            note=v.suggestion.note,
            power=power_out(v.power),
            already_running=v.already_running,
        )
        for v in engine.suggestions(session, settings, now)
    ]


# ---------------------------------------------------------------- reads


class ExperimentsResponse(AgentResponse):
    experiments: list[ExperimentOut]
    suggestions: list[SuggestionOut]
    pending_approvals: list[ApprovalOut]


def get_experiments(
    session: Session, settings: Settings, include_suggestions: bool = True, now: datetime | None = None
) -> ExperimentsResponse:
    t = _now(now)
    exps = [experiment_out(session, e, t) for e in engine.list_all(session, settings)]
    running = [e for e in exps if e.status in ("active", "scheduled", "paused")]
    pending = [approval_out(session, a) for a in engine.list_approvals(session, settings)]
    summary = (
        f"{len(running)} experiment(s) running or scheduled, {len(exps) - len(running)} other; "
        f"{len(pending)} approval(s) waiting for the owner."
    )
    return ExperimentsResponse(
        **_envelope(session, settings, summary),
        experiments=exps,
        suggestions=suggestion_outs(session, settings, t) if include_suggestions else [],
        pending_approvals=pending,
    )


class ExperimentResponse(AgentResponse):
    experiment: ExperimentOut | None
    readouts: list[ReadoutOut] = Field(default_factory=list)
    approvals: list[ApprovalOut] = Field(default_factory=list)
    deviations: list[DeviationOut] = Field(default_factory=list)
    elond: list[ELondOut] = Field(default_factory=list)
    protocol_versions: list[ProtocolOut] = Field(default_factory=list)
    upcoming_slots: list[RegisteredSlotOut] = Field(default_factory=list)


def _failure(session: Session, settings: Settings, exc: Exception, **fields: Any) -> dict[str, Any]:
    return {**_envelope(session, settings, f"Not done: {exc}."), **fields}


def get_experiment(
    session: Session, settings: Settings, experiment: str, now: datetime | None = None
) -> ExperimentResponse:
    t = _now(now)
    try:
        exp = engine.get_experiment(session, settings, experiment)
    except ExperimentError as exc:
        return ExperimentResponse(**_failure(session, settings, exc), experiment=None)
    out = experiment_out(session, exp, t)
    readouts = session.scalars(
        select(Readout).where(Readout.experiment_id == exp.id).order_by(Readout.id)
    ).all()
    latest = readouts[-1] if readouts else None
    summary = f"{exp.key} ({exp.name}) is {exp.status}"
    if latest is not None:
        summary += f"; last look {latest.look}: {latest.decision.replace('_', ' ')}"
    if out.next_look:
        after = f"{out.next_look.after:%Y-%m-%d %H:%M}"
        summary += f"; next look {out.next_look.look} at the first run after {after} UTC"
    caveats = []
    if exp.pause_reason:
        caveats.append(f"Paused: {exp.pause_reason}")
    for f, info in (exp.paused_arms or {}).items():
        caveats.append(f"Arm {f} paused by the arm-harm guardrail at {info.get('at', '?')[:10]}.")
    return ExperimentResponse(
        **_envelope(session, settings, summary + ".", caveats),
        experiment=out,
        readouts=[readout_out(exp, r) for r in readouts],
        approvals=[
            approval_out(session, a)
            for a in session.scalars(
                select(Approval).where(Approval.experiment_id == exp.id).order_by(Approval.id)
            )
        ],
        deviations=[
            DeviationOut(
                slot=f"slot:{d.slot_id}" if d.slot_id else None,
                factor=d.factor,
                kind=d.kind,
                detail=d.detail,
                created_at=d.created_at,
            )
            for d in session.scalars(
                select(Deviation).where(Deviation.experiment_id == exp.id).order_by(Deviation.id)
            )
        ],
        elond=[
            ELondOut(
                experiment=exp.key,
                factor=e.factor,
                t=e.t,
                e_value=e.e_value,
                alpha_t=e.alpha_t,
                rejected=e.rejected,
                randomization_p=e.randomization_p,
            )
            for e in session.scalars(select(ELondEntry).where(ELondEntry.experiment_id == exp.id))
        ],
        protocol_versions=[
            ProtocolOut(
                version=p.version,
                hash=p.hash,
                created_by=p.created_by,
                created_at=p.created_at,
                reason=p.reason,
                protocol=p.protocol,
            )
            for p in session.scalars(
                select(ProtocolVersion)
                .where(ProtocolVersion.experiment_id == exp.id)
                .order_by(ProtocolVersion.version)
            )
        ],
        upcoming_slots=upcoming_slots(session, settings, exp, t),
    )


def slot_out(
    session: Session, settings: Settings, slot_id: int, cancelled: bool = False
) -> RegisteredSlotOut:
    slot = session.get(PlannedSlot, slot_id)
    assert slot is not None
    tz = engine.account_tz(session, settings)
    fc = session.scalar(
        select(Forecast)
        .where(Forecast.subject == f"slot:{slot.id}", Forecast.kind == "treatment_blind")
        .order_by(Forecast.id)
        .limit(1)
    )
    assert fc is not None
    effective = engine.effective_arms(session, slot.id)
    arms = []
    for exp_key, factors in ((slot.lever_context or {}).get("experiments") or {}).items():
        for factor, info in factors.items():
            arm = effective.get(f"{exp_key}.{factor}", info["arm"])
            block = session.scalar(
                select(Assignment.block_key)
                .join(Experiment, Experiment.id == Assignment.experiment_id)
                .where(Assignment.slot_id == slot.id, Assignment.factor == factor, Experiment.key == exp_key)
            )
            arms.append(
                SlotArmOut(
                    experiment=exp_key,
                    factor=factor,
                    lever=info["lever"],
                    arm="treatment" if arm else "control",
                    value=str(info["value"]),
                    block=block or "",
                    measured=bool(info.get("measured", True)),
                )
            )
    local = slot.planned_at.astimezone(tz)
    if cancelled or slot.state == "cancelled":
        instructions = "Not used: the day's switchback condition picked the other slot list. Don't post here."
    elif arms:
        levers = "; ".join(f"{a.lever} = {a.value}" for a in arms)
        instructions = f"Post at {local:%Y-%m-%d %H:%M} ({tz.key}) within 30 minutes, with {levers}."
    else:
        instructions = f"Post at {local:%Y-%m-%d %H:%M} ({tz.key}); no factor applies to this slot."
    q = fc.quantiles
    return RegisteredSlotOut(
        slot=f"slot:{slot.id}",
        planned_at=slot.planned_at,
        planned_local=local.strftime("%Y-%m-%d %H:%M"),
        post_type=slot.post_type,
        labels=slot.labels or {},
        state=slot.state,
        blind_forecast=f"forecast:{fc.id}",
        blind_manifest_hash=fc.manifest_hash,
        blind_frozen_at=fc.frozen_at,
        blind_q10=round(float(q["q10"]), 1),
        blind_q50=round(float(q["q50"]), 1),
        blind_q90=round(float(q["q90"]), 1),
        arms=arms,
        instructions=instructions,
    )


def upcoming_slots(
    session: Session, settings: Settings, exp: Experiment, now: datetime
) -> list[RegisteredSlotOut]:
    ids = session.scalars(
        select(PlannedSlot.id)
        .join(Assignment, Assignment.slot_id == PlannedSlot.id)
        .where(Assignment.experiment_id == exp.id, PlannedSlot.planned_at > now - timedelta(hours=1))
        .distinct()
        .order_by(PlannedSlot.id)
    ).all()
    return [slot_out(session, settings, i) for i in ids]


class ReadoutResponse(AgentResponse):
    experiment: str | None
    readout: ReadoutOut | None
    history: list[ReadoutOut] = Field(default_factory=list)


def get_readout(session: Session, settings: Settings, experiment: str) -> ReadoutResponse:
    try:
        exp = engine.get_experiment(session, settings, experiment)
    except ExperimentError as exc:
        return ReadoutResponse(**_failure(session, settings, exc), experiment=None, readout=None)
    rows = session.scalars(select(Readout).where(Readout.experiment_id == exp.id).order_by(Readout.id)).all()
    if not rows:
        nxt = monitor.next_look(exp, _now(None))
        when = f" The first look is at the first run after {nxt[1]:%Y-%m-%d %H:%M} UTC." if nxt else ""
        return ReadoutResponse(
            **_envelope(
                session,
                settings,
                f"{exp.key} has no readout yet: looks happen only at week boundaries.{when}",
            ),
            experiment=exp.key,
            readout=None,
        )
    latest = readout_out(exp, rows[-1])
    parts = ", ".join(f"{k}: {v}" for k, v in latest.labels.items())
    caveats = []
    if not latest.health_checks.get("ok", True):
        caveats.append("A health check flagged this readout; see health_checks before acting on it.")
    if any(v.get("posterior_disagrees_with_randomization_test") for v in latest.factors.values()):
        caveats.append("The posterior and the randomization test disagree: send this readout to a human.")
    return ReadoutResponse(
        **_envelope(
            session,
            settings,
            f"{exp.key} {latest.look} readout ({'final' if latest.final else 'interim'}): {parts}. "
            "Only e-LOND rejections are labelled confirmed.",
            caveats,
        ),
        experiment=exp.key,
        readout=latest,
        history=[readout_out(exp, r) for r in rows],
    )


class ApprovalsResponse(AgentResponse):
    approvals: list[ApprovalOut]


def list_approvals(session: Session, settings: Settings, status: str | None = "pending") -> ApprovalsResponse:
    rows = [approval_out(session, a) for a in engine.list_approvals(session, settings, status)]
    return ApprovalsResponse(
        **_envelope(
            session,
            settings,
            f"{len(rows)} {status or ''} approval(s). Agents can't decide approvals; the owner does, "
            "on the Experiments screen or with `socialmgr approve <id>`.",
        ),
        approvals=rows,
    )


# ---------------------------------------------------------------- power and proposals


class PowerResponse(AgentResponse):
    power: PowerOut | None
    starts_directly: bool
    needs_owner_because: list[str]


def _spec_for(suggestion_key: str | None, spec: ExperimentSpec | None) -> ExperimentSpec:
    if (suggestion_key is None) == (spec is None):
        raise ExperimentError("give either a suggestion key (E01-E09) or a custom spec")
    if suggestion_key is not None:
        sug = BY_KEY.get(suggestion_key.upper())
        if sug is None:
            raise ExperimentError(f"unknown suggestion {suggestion_key!r}; choose one of E01-E09")
        return sug.spec
    assert spec is not None
    return spec


def power_check(
    session: Session,
    settings: Settings,
    suggestion_key: str | None = None,
    spec: ExperimentSpec | None = None,
    now: datetime | None = None,
) -> PowerResponse:
    """The three-number preview, and whether proposing it now would start directly."""
    try:
        s = _spec_for(suggestion_key, spec)
    except ExperimentError as exc:
        return PowerResponse(
            **_failure(session, settings, exc), power=None, starts_directly=False, needs_owner_because=[]
        )
    t = _now(now)
    p = engine.power_check(session, settings, s, t)
    probe = Experiment(
        id=-1,
        key=s.key or "draft",
        design=s.design,
        factors=[f.model_dump(mode="json") for f in s.factors],
        horizon_weeks=s.horizon_weeks,
    )
    reasons = engine.approval_reasons(session, settings, probe)
    pw = p.power
    summary = (
        f"At {p.posts_per_day:g} posts/day over {s.horizon_weeks:g} weeks: power "
        f"{_fmt(pw.get('1.25x'))} at 1.25x, {_fmt(pw.get('1.5x'))} at 1.5x, {_fmt(pw.get('2x'))} at 2x"
        + (f"; 80% power needs a {p.mde_ratio_80:g}x lift." if p.mde_ratio_80 else ".")
    )
    return PowerResponse(
        **_envelope(session, settings, summary),
        power=power_out(p),
        starts_directly=not reasons,
        needs_owner_because=reasons,
    )


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


class ProposeResponse(AgentResponse):
    created: bool
    started: bool
    experiment: ExperimentOut | None
    power: PowerOut | None
    approval: ApprovalOut | None


def propose_experiment(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    suggestion_key: str | None = None,
    spec: ExperimentSpec | None = None,
    start: bool = True,
    now: datetime | None = None,
) -> ProposeResponse:
    t = _now(now)
    try:
        r = engine.propose_experiment(
            session,
            settings,
            plugins,
            actor=ACTOR,
            now=t,
            suggestion_key=suggestion_key,
            spec=spec,
            start=start,
        )
    except (ExperimentError, ValueError) as exc:
        session.rollback()
        return ProposeResponse(
            **_failure(session, settings, exc),
            created=False,
            started=False,
            experiment=None,
            power=None,
            approval=None,
        )
    session.commit()
    exp = r.experiment
    if r.approval is not None:
        summary = (
            f"Proposed {exp.key}; it needs the owner's approval ({'; '.join(r.reasons)}): "
            f"approval:{r.approval.id} is pending."
        )
    elif exp.status == "scheduled":
        summary = f"Proposed and scheduled {exp.key}. Register slots (register_slots) to draw its first arms."
    else:
        summary = f"Proposed {exp.key} as a draft; schedule it with schedule_experiment."
    return ProposeResponse(
        **_envelope(session, settings, summary),
        created=True,
        started=exp.status == "scheduled",
        experiment=experiment_out(session, exp, t),
        power=power_out(r.power),
        approval=approval_out(session, r.approval) if r.approval else None,
    )


class ExperimentActionResponse(AgentResponse):
    done: bool
    experiment: ExperimentOut | None
    approval: ApprovalOut | None = None


def schedule_experiment(
    session: Session, settings: Settings, experiment: str, now: datetime | None = None
) -> ExperimentActionResponse:
    t = _now(now)
    try:
        r = engine.schedule_experiment(session, settings, experiment, actor=ACTOR, now=t)
    except ExperimentError as exc:
        session.rollback()
        return ExperimentActionResponse(**_failure(session, settings, exc), done=False, experiment=None)
    session.commit()
    summary = (
        f"{r.experiment.key} waits for the owner ({'; '.join(r.reasons)})."
        if r.approval
        else f"{r.experiment.key} is scheduled; register slots to start it."
    )
    return ExperimentActionResponse(
        **_envelope(session, settings, summary),
        done=r.approval is None,
        experiment=experiment_out(session, r.experiment, t),
        approval=approval_out(session, r.approval) if r.approval else None,
    )


def stop_experiment(
    session: Session, settings: Settings, experiment: str, reason: str, now: datetime | None = None
) -> ExperimentActionResponse:
    t = _now(now)
    try:
        exp = engine.stop_experiment(session, settings, experiment, actor=ACTOR, reason=reason, now=t)
    except ExperimentError as exc:
        session.rollback()
        return ExperimentActionResponse(**_failure(session, settings, exc), done=False, experiment=None)
    session.commit()
    return ExperimentActionResponse(
        **_envelope(
            session,
            settings,
            f"Stopped {exp.key}. Its planned treated slots revert to control; no e-value is emitted.",
        ),
        done=True,
        experiment=experiment_out(session, exp, t),
    )


class AmendResponse(AgentResponse):
    experiment: ExperimentOut | None
    applied: list[str]
    protocol_version: int | None
    approvals: list[ApprovalOut]


def amend_experiment(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins | None,
    experiment: str,
    changes: AmendRequest,
    reason: str,
    now: datetime | None = None,
) -> AmendResponse:
    t = _now(now)
    try:
        r = engine.amend_experiment(
            session, settings, plugins, experiment, changes, actor=ACTOR, reason=reason, now=t
        )
    except (ExperimentError, ValueError) as exc:
        session.rollback()
        return AmendResponse(
            **_failure(session, settings, exc),
            experiment=None,
            applied=[],
            protocol_version=None,
            approvals=[],
        )
    session.commit()
    parts = []
    if r.applied:
        parts.append(f"applied {', '.join(r.applied)} as protocol v{r.protocol_version}")
    if r.approvals:
        parts.append(
            "waiting for the owner: " + ", ".join(f"approval:{a.id} ({a.kind})" for a in r.approvals)
        )
    return AmendResponse(
        **_envelope(session, settings, f"{r.experiment.key}: {'; '.join(parts) or 'nothing changed'}."),
        experiment=experiment_out(session, r.experiment, t),
        applied=r.applied,
        protocol_version=r.protocol_version,
        approvals=[approval_out(session, a) for a in r.approvals],
    )


def extend_experiment(
    session: Session, settings: Settings, experiment: str, now: datetime | None = None
) -> AmendResponse:
    """MODEL §6.3's one extension of up to 50% (the agent may start it; over 8 weeks needs the owner)."""
    t = _now(now)
    try:
        r = engine.extend_experiment(session, settings, experiment, actor=ACTOR, now=t)
    except ExperimentError as exc:
        session.rollback()
        return AmendResponse(
            **_failure(session, settings, exc),
            experiment=None,
            applied=[],
            protocol_version=None,
            approvals=[],
        )
    session.commit()
    summary = (
        f"{r.experiment.key} extended to {r.experiment.horizon_weeks:g} weeks."
        if r.applied
        else f"{r.experiment.key}: the extension waits for the owner (approval:{r.approvals[0].id})."
    )
    return AmendResponse(
        **_envelope(session, settings, summary),
        experiment=experiment_out(session, r.experiment, t),
        applied=r.applied,
        protocol_version=r.protocol_version,
        approvals=[approval_out(session, a) for a in r.approvals],
    )


# ---------------------------------------------------------------- slots


class RegisterSlotsResponse(AgentResponse):
    registered: bool
    experiments: list[str]
    slots: list[RegisteredSlotOut]
    warnings: list[str] = Field(default_factory=list)


def register_slots(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    days: list[DayPlan],
    experiments: list[str] | None = None,
    now: datetime | None = None,
) -> RegisterSlotsResponse:
    """Whole local days up to 7 ahead. Each slot's treatment-blind forecast is frozen before its
    arms are drawn; the response says what to post in each slot."""
    t = _now(now)
    try:
        r = engine.register_slots(
            session, settings, plugins, days, now=t, actor=ACTOR, experiment_keys=experiments
        )
    except (ExperimentError, ValueError) as exc:
        session.rollback()
        return RegisterSlotsResponse(
            **_failure(session, settings, exc), registered=False, experiments=[], slots=[]
        )
    session.commit()
    slots = [slot_out(session, settings, rs.slot.id, rs.cancelled) for rs in r.slots]
    used = [s for s in slots if s.state != "cancelled"]
    summary = (
        f"Registered {len(used)} slot(s) over {len(days)} day(s) for {', '.join(r.experiments)}; "
        "blind forecasts were frozen before the draw. "
        "Post within 30 minutes of each slot, with the levers it lists."
    )
    return RegisterSlotsResponse(
        **_envelope(session, settings, summary, list(r.warnings)),
        registered=True,
        experiments=r.experiments,
        slots=slots,
        warnings=r.warnings,
    )
