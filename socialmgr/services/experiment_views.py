"""Experiments and Account "Next posts" for the web app (plan §1, §7): typed read views and the
owner's actions.

The web app is the owner's surface. Its writes call the engine with ``actor="owner"`` and its
approve / reject go through ``services/approvals.py``. The MCP server never imports this module;
agents use ``services/experiments.py``, which acts as ``actor="agent"`` and can't decide approvals.

Reads only use the engine's and monitor's read helpers (``slot_outcomes``, ``effective_arms``,
``next_look``, ``label``, ``approval_reasons``); no statistic is computed here.
"""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.api import schemas as s
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
    ScoreRow,
)
from socialmgr.experiments import engine, instructions, monitor
from socialmgr.experiments.engine import ExperimentError
from socialmgr.experiments.specs import Actor, AppliesTo, DayPlan, ExperimentSpec, FactorSpec
from socialmgr.modelling.scoring import surprise_label
from socialmgr.services import approvals
from socialmgr.services.agent import _envelope
from socialmgr.services.plugins import ActivePlugins

OWNER: Actor = "owner"
RUNNING = ("active", "scheduled", "paused")
WAITING = ("pending_approval", "draft")
ENDED = ("concluded", "stopped")
HEADLINES = {
    "adopt": "Adopt (not confirmed)",
    "reject": "Reject",
    "near_threshold": "Near threshold",
    "continue": "Keep going",
}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC).replace(microsecond=0)


def _aware(t: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored time is UTC."""
    if t is None:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


# ---------------------------------------------------------------- small pieces


def factor_out(raw: dict[str, Any]) -> s.FactorOut:
    f = FactorSpec.model_validate(raw)
    return s.FactorOut(
        name=f.name,
        lever=f.lever,
        lever_label=instructions.lever_label(f.lever),
        control=f.control,
        treatment=f.treatment,
        applies_to=instructions.applies_to_text(f.applies_to),
    )


def _fmt_ratio(x: float) -> str:
    return f"{x:.2f}×"


def _status(session: Session, exp: Experiment) -> tuple[str, str]:
    if exp.status == "active":
        return "Running", "accent"
    if exp.status == "scheduled":
        return "Scheduled", "default"
    if exp.status == "paused":
        return "Paused", "warn"
    if exp.status == "pending_approval":
        return "Needs approval", "warn"
    if exp.status == "draft":
        return "Draft", "default"
    if exp.status == "stopped":
        return "Stopped", "negative"
    final = session.scalar(
        select(Readout)
        .where(Readout.experiment_id == exp.id, Readout.final.is_(True))
        .order_by(Readout.id.desc())
    )
    decisions = {str(v.get("decision")) for v in (final.factors if final else {}).values()}
    confirmed = any(v.get("confirmed") for v in (final.factors if final else {}).values())
    if decisions == {"adopt"}:
        return ("Adopt · confirmed", "positive") if confirmed else ("Adopt (not confirmed)", "positive")
    if decisions == {"reject"}:
        return "Reject", "default"
    if decisions:
        return "Mixed", "default"
    return "Concluded", "default"


def summary_out(session: Session, exp: Experiment, now: datetime) -> s.ExperimentSummaryOut:
    started = _aware(exp.started_at)
    end = _aware(engine.horizon_end(exp))
    weeks_total = math.ceil(exp.horizon_weeks)
    week = None
    if started is not None and now >= started:
        week = min(weeks_total, int((now - started) / timedelta(days=7)) + 1)
    outcomes = monitor.slot_outcomes(session, exp)
    latest = session.scalar(
        select(Readout).where(Readout.experiment_id == exp.id).order_by(Readout.id.desc())
    )
    effect, interval, effect_text = None, None, "too early" if exp.status not in WAITING else "not started"
    if latest is not None and exp.factors:
        first = latest.factors.get(exp.factors[0]["name"], {})
        if first.get("effect_ratio") is not None:
            effect = float(first["effect_ratio"])
            interval = [float(x) for x in first["interval80_ratio"]]
            effect_text = f"{_fmt_ratio(effect)} [{interval[0]:.2f}, {interval[1]:.2f}]"
    nxt = monitor.next_look(exp, now)
    label, tone = _status(session, exp)
    return s.ExperimentSummaryOut(
        id=f"exp:{exp.id}",
        key=exp.key,
        name=exp.name,
        hypothesis=exp.hypothesis,
        design=cast(Any, exp.design),
        design_label=instructions.design_label(exp.design),
        status=cast(Any, exp.status),
        status_label=label,
        status_tone=cast(Any, tone),
        factors=[factor_out(f) for f in exp.factors],
        primary_metric=exp.primary_metric,
        primary_metric_label=instructions.metric_label(exp.primary_metric),
        delta_ratio=round(math.exp(exp.delta), 4),
        horizon_weeks=exp.horizon_weeks,
        weeks_total=weeks_total,
        week=week,
        created_by=exp.created_by,
        created_at=exp.created_at,
        started_at=started,
        horizon_end=end,
        concluded_at=_aware(exp.concluded_at),
        slots_assigned=len(outcomes),
        posts_matured=sum(1 for o in outcomes if o.y is not None),
        next_look=s.NextLookOut(look=nxt[0], after=_aware(nxt[1]) or nxt[1]) if nxt else None,
        latest_look=latest.look if latest else None,
        effect_ratio=effect,
        effect_interval80=interval,
        effect_text=effect_text,
        outcome=exp.outcome,
        paused_arms=[
            s.PausedArmOut(
                factor=f,
                at=datetime.fromisoformat(info["at"]) if info.get("at") else None,
                detail=(
                    f"arm-harm guardrail: upper bound {math.exp(float(info['bound'])):.2f}× is below 0.8×"
                    if info.get("bound") is not None
                    else str(info.get("reason") or "arm-harm guardrail")
                ),
            )
            for f, info in (exp.paused_arms or {}).items()
        ],
        pause_reason=exp.pause_reason,
        extension_used=exp.extension_used,
        extension_offered=exp.extension_offered_at is not None and not exp.extension_used,
        pending_approvals=sum(
            1
            for _ in session.scalars(
                select(Approval.id).where(Approval.experiment_id == exp.id, Approval.status == "pending")
            )
        ),
    )


def approval_card(session: Session, a: Approval) -> s.ApprovalCardOut:
    exp = session.get(Experiment, a.experiment_id) if a.experiment_id else None
    key = exp.key if exp else "?"
    payload = a.payload or {}
    if a.kind == "start_experiment":
        title = f"Start {key}: {exp.name}" if exp else "Start an experiment"
    elif a.kind == "amend_protocol":
        changes = dict(payload.get("changes") or {})
        if changes.get("extension") and exp is not None:
            title = f"Extend {key} from {exp.horizon_weeks:g} to {float(changes['horizon_weeks']):g} weeks"
        else:
            title = f"Amend {key}: " + ", ".join(k.replace("_", " ") for k in changes if k != "extension")
    elif a.kind == "exclude_posts":
        n = len(payload.get("slot_ids") or [])
        title = f"Exclude {n} post{'s' if n != 1 else ''} from {key}'s analysis"
    elif a.kind == "guardrail_override":
        title = f"Override the guardrail on {key}: resume the paused arm"
    else:
        title = f"{a.kind.replace('_', ' ').capitalize()} for {key}"
    why = "; ".join(a.reasons) if a.reasons else "an owner decision under MODEL §6.9"
    if a.requested_by == "agent":
        detail = (
            f"Requested by an agent via MCP. It needs you because {why}; "
            "agents can't approve their own requests."
        )
    else:
        detail = f"Requested from this app. It needs an explicit approval because {why}."
    return s.ApprovalCardOut(
        id=f"approval:{a.id}",
        approval_id=a.id,
        kind=a.kind,
        experiment=exp.key if exp else None,
        title=title,
        detail=detail,
        reasons=list(a.reasons or []),
        requested_by=a.requested_by,
        requested_at=_aware(a.requested_at) or a.requested_at,
        status=cast(Any, a.status),
        decided_by=a.decided_by,
        decided_at=_aware(a.decided_at),
        note=a.note,
    )


def _power(p: engine.PowerReport) -> s.PowerPreviewOut:
    def num(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.2f}"

    pw = p.power
    return s.PowerPreviewOut(
        posts_per_day=p.posts_per_day,
        n_posts=p.n_posts,
        power_125=pw.get("1.25x"),
        power_15=pw.get("1.5x"),
        power_2=pw.get("2x"),
        mde_ratio_80=p.mde_ratio_80,
        text=f"1.25×: {num(pw.get('1.25x'))} · 1.5×: {num(pw.get('1.5x'))} · 2×: {num(pw.get('2x'))}",
        note=p.note,
    )


def suggestion_cards(session: Session, settings: Settings, now: datetime) -> list[s.SuggestionCardOut]:
    out = []
    for v in engine.suggestions(session, settings, now):
        spec = v.suggestion.spec
        probe = Experiment(
            id=-1,
            key=spec.key or "draft",
            design=spec.design,
            factors=[f.model_dump(mode="json") for f in spec.factors],
            horizon_weeks=spec.horizon_weeks,
            created_by=OWNER,
        )
        out.append(
            s.SuggestionCardOut(
                key=v.suggestion.key,
                name=spec.name,
                hypothesis=spec.hypothesis,
                mechanism=v.suggestion.mechanism,
                design=spec.design,
                design_label=instructions.design_label(spec.design),
                factor=factor_out(spec.factors[0].model_dump(mode="json")),
                primary_metric_label=instructions.metric_label(spec.primary_metric),
                horizon_weeks=spec.horizon_weeks,
                horizon_text=v.suggestion.horizon_text,
                detectability=v.suggestion.detectability,
                note=v.suggestion.note,
                power=_power(v.power),
                already_running=v.already_running,
                needs_owner_because=engine.approval_reasons(session, settings, probe),
            )
        )
    return out


# ---------------------------------------------------------------- slots


def _slot_forecast(session: Session, slot_id: int) -> Forecast | None:
    rows = session.scalars(
        select(Forecast).where(Forecast.subject == f"slot:{slot_id}").order_by(Forecast.id)
    ).all()
    return next((f for f in rows if f.kind == "treatment_blind"), rows[0] if rows else None)


def slot_row(
    session: Session, settings: Settings, slot: PlannedSlot, cancelled: bool = False
) -> s.SlotRowOut:
    tz = engine.account_tz(session, settings)
    local = _aware(slot.planned_at).astimezone(tz)  # type: ignore[union-attr]
    effective = engine.effective_arms(session, slot.id)
    arms: list[s.ArmOut] = []
    for key, factors in ((slot.lever_context or {}).get("experiments") or {}).items():
        for factor, info in factors.items():
            drawn = int(info["arm"])
            arm = effective.get(f"{key}.{factor}", drawn)
            exp = session.scalar(
                select(Experiment).where(
                    Experiment.workspace_id == settings.workspace_id, Experiment.key == key
                )
            )
            spec = next((f for f in (exp.factors if exp else []) if f["name"] == factor), None)
            value = (spec["treatment"] if arm else spec["control"]) if spec else str(info["value"])
            arms.append(
                s.ArmOut(
                    experiment=key,
                    factor=factor,
                    lever=str(info["lever"]),
                    arm="treatment" if arm else "control",
                    value=value,
                    instruction=instructions.arm_instruction(str(info["lever"]), value),
                    measured=bool(info.get("measured", True)),
                    reverted=bool(drawn and not arm),
                )
            )
    state = "cancelled" if cancelled else slot.state
    fc = _slot_forecast(session, slot.id)
    q = fc.quantiles if fc else {}
    score = session.scalar(select(ScoreRow).where(ScoreRow.forecast_id == fc.id)) if fc else None
    status: str = state
    if state == "posted":
        status = surprise_label(score.pit_mid) if score is not None else "awaiting"
    local_text = local.strftime("%Y-%m-%d %H:%M")
    return s.SlotRowOut(
        slot=f"slot:{slot.id}",
        planned_at=local.astimezone(UTC),
        planned_local=local_text,
        post_type=slot.post_type,
        labels={k: str(v) for k, v in (slot.labels or {}).items()},
        state=cast(Any, state),
        experiment_slot=bool(arms) or slot.experiment_id is not None,
        text=slot.text,
        arms=arms,
        instructions=instructions.slot_instructions(
            local_text, tz.key, [(a.lever, a.value) for a in arms], cancelled=state == "cancelled"
        ),
        forecast=f"forecast:{fc.id}" if fc else None,
        forecast_kind=fc.kind if fc else None,
        forecast_q10=round(float(q["q10"]), 1) if q else None,
        forecast_q50=round(float(q["q50"]), 1) if q else None,
        forecast_q90=round(float(q["q90"]), 1) if q else None,
        forecast_frozen_at=_aware(fc.frozen_at) if fc else None,
        matched_post=f"post:{slot.matched_post_id}" if slot.matched_post_id else None,
        outcome_i48=round(float(score.outcome), 1) if score is not None and state == "posted" else None,
        pit=round(float(score.pit_mid), 4) if score is not None and state == "posted" else None,
        outcome_status=cast(Any, status),
    )


def _past(exp: Experiment, rows: Sequence[s.SlotRowOut]) -> list[s.FactorPastOut]:
    out = []
    for raw in exp.factors:
        treated, controls = s.ArmOutcomeCountsOut(), s.ArmOutcomeCountsOut()
        for r in rows:
            arm = next((a for a in r.arms if a.experiment == exp.key and a.factor == raw["name"]), None)
            if arm is None:
                continue
            counts = treated if (arm.arm == "treatment" or arm.reverted) else controls  # intention to treat
            if r.outcome_status in ("above", "as_expected", "below", "awaiting"):
                setattr(counts, r.outcome_status, getattr(counts, r.outcome_status) + 1)
            elif r.outcome_status == "missed":
                counts.lost += 1
        out.append(
            s.FactorPastOut(
                factor=raw["name"],
                treatment=raw["treatment"],
                control=raw["control"],
                treated=treated,
                controls=controls,
            )
        )
    return out


# ---------------------------------------------------------------- readouts


def _health(checks: dict[str, Any]) -> list[s.HealthCheckOut]:
    out = []
    for name, value in checks.items():
        if name == "ok" or not isinstance(value, dict):
            continue
        if name == "srm":
            flags = [f for f, v in value.items() if v.get("flag")]
            parts = [f"{f}: {v['treated']} treated / {v['control']} control" for f, v in value.items()]
            out.append(
                s.HealthCheckOut(
                    name="Sample ratio (SRM)", ok=not flags, detail="; ".join(parts) or "no posts"
                )
            )
        elif name == "maturity":
            waiting = value.get("awaiting_capture") or []
            detail = (
                "every assigned post has its 48h capture"
                if not waiting
                else f"{len(waiting)} awaiting capture"
            )
            out.append(
                s.HealthCheckOut(name="48h captures complete", ok=bool(value.get("complete")), detail=detail)
            )
        elif name == "missed_or_deleted_by_arm":
            flags = [f for f, v in value.items() if v.get("flag")]
            parts = [
                f"{f}: {v['treated_lost']} treated / {v['control_lost']} control lost"
                for f, v in value.items()
            ]
            out.append(
                s.HealthCheckOut(name="Missed or deleted by arm", ok=not flags, detail="; ".join(parts))
            )
        elif name == "blind_forecast_predates_draw":
            bad = value.get("violations") or []
            detail = "every blind forecast hash predates its draw" if not bad else f"{len(bad)} violation(s)"
            out.append(
                s.HealthCheckOut(
                    name="Blind forecast before the draw", ok=bool(value.get("ok")), detail=detail
                )
            )
        elif name == "draws_reproduce_from_seed":
            ok = bool(value.get("ok"))
            detail = "re-drawn from the protocol seed" if ok else "a draw differs from the seed"
            out.append(s.HealthCheckOut(name="Draws reproduce from the seed", ok=ok, detail=detail))
        elif name == "regime_change_in_window":
            regimes = value.get("regimes") or []
            detail = "none" if not regimes else "a regime fixed effect applies: " + ", ".join(regimes)
            out.append(
                s.HealthCheckOut(name="Regime change in the window", ok=not value.get("flag"), detail=detail)
            )
        else:
            ok = not value.get("flag") and value.get("ok", True) is not False
            out.append(s.HealthCheckOut(name=name.replace("_", " ").capitalize(), ok=ok, detail=""))
    return out


def _elond_text(decision: str, entry: ELondEntry | None) -> str:
    if entry is None:
        if decision == "adopt":
            return "adopt (not confirmed): it enters e-LOND when the experiment concludes"
        return "enters e-LOND when the experiment concludes"
    if entry.rejected:
        return f"confirmed: e-LOND rejected the null (e = {entry.e_value:.2f} ≥ {1 / entry.alpha_t:.0f})"
    bar = 1 / entry.alpha_t
    return f"{decision} (not confirmed): e = {entry.e_value:.2f}, below the e-LOND bar of {bar:.0f}"


def readout_view(session: Session, exp: Experiment, r: Readout) -> s.ReadoutViewOut:
    specs = {f["name"]: f for f in exp.factors}
    entries = {
        e.factor: e for e in session.scalars(select(ELondEntry).where(ELondEntry.experiment_id == exp.id))
    }
    factors = []
    for name, v in r.factors.items():
        spec = specs.get(name, {})
        decision = str(v.get("decision", "continue"))
        confirmed = bool(v.get("confirmed"))
        headline = (
            "Adopt · confirmed" if decision == "adopt" and confirmed else HEADLINES.get(decision, decision)
        )
        interval = v.get("interval80_ratio")
        factors.append(
            s.FactorReadoutOut(
                factor=name,
                lever=str(spec.get("lever", "")),
                control=str(spec.get("control", "")),
                treatment=str(spec.get("treatment", "")),
                decision=cast(Any, decision),
                label=monitor.label(v),
                headline=headline,
                reason=str(v.get("reason", "")),
                decided_at_look=v.get("decided_at_look"),
                effect_ratio=v.get("effect_ratio"),
                interval80=[float(x) for x in interval] if interval else None,
                p_gt_delta=v.get("p_gt_delta"),
                p_lt_zero=v.get("p_lt_zero"),
                expected_loss=v.get("expected_loss"),
                n_treated=int(v.get("n_treated", 0)),
                n_control=int(v.get("n_control", 0)),
                randomization_p=v.get("randomization_p"),
                disagrees_with_randomization_test=bool(v.get("posterior_disagrees_with_randomization_test")),
                confirmed=confirmed,
                elond=_elond_text(decision, entries.get(name) if r.final else None),
                e_value=v.get("e_value"),
            )
        )
    return s.ReadoutViewOut(
        id=f"readout:{r.id}",
        look=r.look,
        experiment_week=r.experiment_week,
        final=r.final,
        decision=r.decision,
        created_at=_aware(r.created_at) or r.created_at,
        protocol_version=r.protocol_version,
        factors=factors,
        health=_health(r.health_checks or {}),
        health_ok=bool((r.health_checks or {}).get("ok", True)),
    )


# ---------------------------------------------------------------- reads


def experiments_overview(
    session: Session, settings: Settings, now: datetime | None = None
) -> s.ExperimentsOut:
    t = _now(now)
    exps = engine.list_all(session, settings)
    views = {e.id: summary_out(session, e, t) for e in exps}
    running = [views[e.id] for e in exps if e.status in RUNNING]
    waiting = [views[e.id] for e in exps if e.status in WAITING]
    ended = sorted(
        (views[e.id] for e in exps if e.status in ENDED),
        key=lambda v: v.concluded_at or v.created_at,
        reverse=True,
    )
    pending = [approval_card(session, a) for a in engine.list_approvals(session, settings)]
    entries = session.scalars(
        select(ELondEntry).where(ELondEntry.workspace_id == settings.workspace_id)
    ).all()
    summary = (
        f"{len(running)} running or scheduled, {len(waiting)} waiting, {len(ended)} concluded; "
        f"{len(pending)} approval(s) waiting for you."
    )
    return s.ExperimentsOut(
        **_envelope(session, settings, summary),
        running=running,
        waiting=waiting,
        concluded=ended,
        suggestions=suggestion_cards(session, settings, t),
        approvals=pending,
        elond=s.ELondStatusOut(
            tests=len(entries),
            rejections=sum(1 for e in entries if e.rejected),
            alpha=entries[0].alpha if entries else monitor.ELOND_ALPHA,
        ),
        posts_per_week=round(engine.posting_rate(session, settings, t) * 7, 1),
        tz=engine.account_tz(session, settings).key,
    )


def experiment_detail(
    session: Session, settings: Settings, ref: str, now: datetime | None = None
) -> s.ExperimentDetailOut | None:
    t = _now(now)
    try:
        exp = engine.get_experiment(session, settings, ref.removeprefix("exp:"))
    except ExperimentError:
        return None
    slot_ids = session.scalars(
        select(Assignment.slot_id)
        .where(Assignment.experiment_id == exp.id)
        .distinct()
        .order_by(Assignment.slot_id)
    ).all()
    slots = sorted(
        (slot_row(session, settings, cast(PlannedSlot, session.get(PlannedSlot, i))) for i in slot_ids),
        key=lambda r: r.planned_at,
    )
    readouts = [
        readout_view(session, exp, r)
        for r in session.scalars(select(Readout).where(Readout.experiment_id == exp.id).order_by(Readout.id))
    ]
    out = summary_out(session, exp, t)
    summary = f"{exp.key} ({exp.name}) is {out.status_label.lower()}"
    if readouts:
        summary += f"; last look {readouts[-1].look}: {readouts[-1].decision.replace('_', ' ')}"
    caveats = []
    if readouts and not readouts[-1].health_ok:
        caveats.append("A health check flagged the latest readout; read the checks before acting on it.")
    if any(f.disagrees_with_randomization_test for f in (readouts[-1].factors if readouts else [])):
        caveats.append(
            "The posterior and the randomization test disagree: a human should review this readout."
        )
    proto = engine.latest_protocol(session, exp)
    return s.ExperimentDetailOut(
        **_envelope(session, settings, summary + ".", caveats),
        experiment=out,
        tz=engine.account_tz(session, settings).key,
        readout=readouts[-1] if readouts else None,
        readouts=readouts,
        slots=slots,
        past=_past(exp, slots),
        approvals=[
            approval_card(session, a)
            for a in session.scalars(
                select(Approval).where(Approval.experiment_id == exp.id).order_by(Approval.id)
            )
        ],
        deviations=[
            s.DeviationRowOut(
                slot=f"slot:{d.slot_id}" if d.slot_id else None,
                factor=d.factor,
                kind=d.kind,
                detail=d.detail,
                created_at=_aware(d.created_at) or d.created_at,
            )
            for d in session.scalars(
                select(Deviation).where(Deviation.experiment_id == exp.id).order_by(Deviation.id)
            )
        ],
        elond=[
            s.ELondEntryOut(factor=e.factor, t=e.t, e_value=e.e_value, alpha_t=e.alpha_t, rejected=e.rejected)
            for e in session.scalars(select(ELondEntry).where(ELondEntry.experiment_id == exp.id))
        ],
        protocol_versions=[
            s.ProtocolVersionOut(
                version=p.version,
                hash=p.hash,
                created_by=p.created_by,
                created_at=_aware(p.created_at) or p.created_at,
                reason=p.reason,
            )
            for p in session.scalars(
                select(ProtocolVersion)
                .where(ProtocolVersion.experiment_id == exp.id)
                .order_by(ProtocolVersion.version)
            )
        ],
        seed=exp.seed,
        frozen_forecaster=f"{exp.model_plugin} {exp.model_version}" if proto else exp.model_version,
    )


def approvals_list(
    session: Session, settings: Settings, status: str | None = "pending"
) -> list[s.ApprovalCardOut]:
    return [approval_card(session, a) for a in engine.list_approvals(session, settings, status)]


def next_posts(
    session: Session, settings: Settings, days: int = 7, now: datetime | None = None
) -> s.NextPostsOut:
    t = _now(now)
    tz = engine.account_tz(session, settings)
    rows = session.scalars(
        select(PlannedSlot)
        .where(
            PlannedSlot.workspace_id == settings.workspace_id,
            PlannedSlot.planned_at > t - timedelta(minutes=30),
            PlannedSlot.planned_at <= t + timedelta(days=days),
            PlannedSlot.state != "cancelled",
        )
        .order_by(PlannedSlot.planned_at)
    ).all()
    taken = session.scalars(
        select(PlannedSlot.planned_at).where(
            PlannedSlot.workspace_id == settings.workspace_id,
            PlannedSlot.experiment_id.is_not(None),
            PlannedSlot.state != "cancelled",
            PlannedSlot.planned_at > t - timedelta(days=1),
        )
    ).all()
    registered = sorted({(_aware(p) or p).astimezone(tz).date().isoformat() for p in taken})
    return s.NextPostsOut(
        tz=tz.key,
        days=days,
        slots=[slot_row(session, settings, r) for r in rows],
        drawing=[e.key for e in engine.list_all(session, settings, engine.DRAWING_STATUSES)],
        registered_days=registered,
    )


# ---------------------------------------------------------------- owner actions


def _action(
    session: Session,
    done: bool,
    summary: str,
    exp: Experiment | None,
    approval: Approval | None,
    now: datetime,
) -> s.ExperimentActionOut:
    return s.ExperimentActionOut(
        done=done,
        summary=summary,
        experiment=summary_out(session, exp, now) if exp is not None else None,
        approval=approval_card(session, approval) if approval is not None else None,
    )


def custom_spec(body: s.CustomSpecIn) -> ExperimentSpec:
    return ExperimentSpec(
        name=body.name,
        hypothesis=body.hypothesis,
        factors=[
            FactorSpec(
                name=body.lever,
                lever=body.lever,
                control=body.control,
                treatment=body.treatment,
                applies_to=AppliesTo(),
            )
        ],
        horizon_weeks=body.horizon_weeks,
    )


def propose(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    body: s.ProposeIn,
    now: datetime | None = None,
) -> s.ExperimentActionOut:
    """Start E01-E09 or a custom experiment as the owner: scheduled directly, or a pending approval
    for MODEL §6.9's owner items (horizons over 8 weeks, a 4th concurrent experiment, ...)."""
    t = _now(now)
    try:
        if (body.suggestion is None) == (body.custom is None):
            raise ExperimentError("give either a suggestion (E01-E09) or a custom experiment")
        spec = custom_spec(body.custom) if body.custom is not None else None
        r = engine.propose_experiment(
            session, settings, plugins, actor=OWNER, now=t, suggestion_key=body.suggestion, spec=spec
        )
    except (ExperimentError, ValueError) as exc:
        session.rollback()
        return _action(session, False, f"Not started: {exc}.", None, None, t)
    session.commit()
    exp = r.experiment
    if r.approval is not None:
        summary = f"{exp.key} waits for your approval: {'; '.join(r.reasons)}."
    else:
        summary = f"{exp.key} is scheduled. Register slots to draw its first arms."
    return _action(session, True, summary, exp, r.approval, t)


def register_slots(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    days: list[DayPlan],
    keys: list[str] | None = None,
    now: datetime | None = None,
) -> s.RegisterSlotsOut:
    """Whole local days up to 7 ahead, for every scheduled or active experiment (or ``keys``):
    blind forecasts are frozen first, then the arms are drawn."""
    t = _now(now)
    try:
        r = engine.register_slots(session, settings, plugins, days, now=t, actor=OWNER, experiment_keys=keys)
    except (ExperimentError, ValueError) as exc:
        session.rollback()
        return s.RegisterSlotsOut(
            registered=False, summary=f"Not registered: {exc}.", experiments=[], warnings=[], slots=[]
        )
    session.commit()
    rows = [slot_row(session, settings, rs.slot, rs.cancelled) for rs in r.slots]
    used = sum(1 for row in rows if row.state != "cancelled")
    return s.RegisterSlotsOut(
        registered=True,
        summary=(
            f"Registered {used} slot(s) over {len(days)} day(s) for {', '.join(r.experiments)}. "
            "Blind forecasts were frozen before the draw; post within 30 minutes of each slot."
        ),
        experiments=r.experiments,
        warnings=list(r.warnings),
        slots=rows,
    )


def stop(
    session: Session, settings: Settings, ref: str, reason: str, now: datetime | None = None
) -> s.ExperimentActionOut:
    t = _now(now)
    try:
        exp = engine.stop_experiment(session, settings, ref, actor=OWNER, reason=reason, now=t)
    except ExperimentError as exc:
        session.rollback()
        return _action(session, False, f"Not stopped: {exc}.", None, None, t)
    session.commit()
    return _action(
        session,
        True,
        f"Stopped {exp.key}. Its planned treated slots revert to control; no e-value is emitted.",
        exp,
        None,
        t,
    )


def extend(
    session: Session, settings: Settings, ref: str, now: datetime | None = None
) -> s.ExperimentActionOut:
    t = _now(now)
    try:
        r = engine.extend_experiment(session, settings, ref, actor=OWNER, now=t)
    except ExperimentError as exc:
        session.rollback()
        return _action(session, False, f"Not extended: {exc}.", None, None, t)
    session.commit()
    if r.applied:
        return _action(
            session,
            True,
            f"{r.experiment.key} extended to {r.experiment.horizon_weeks:g} weeks.",
            r.experiment,
            None,
            t,
        )
    return _action(
        session,
        True,
        f"{r.experiment.key}: the extension takes it over 8 weeks, so it waits for your approval.",
        r.experiment,
        r.approvals[0] if r.approvals else None,
        t,
    )


def decide(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins | None,
    approval_id: int,
    decision: str,
    note: str | None = None,
    now: datetime | None = None,
) -> s.DecideOut:
    """The owner approves or rejects (``services/approvals.py``)."""
    try:
        approvals.decide(session, settings, approval_id, decision, note=note, plugins=plugins, now=_now(now))
    except ExperimentError as exc:
        session.rollback()
        row = session.get(Approval, approval_id)
        card = approval_card(session, row) if row else None
        return s.DecideOut(done=False, summary=f"Not decided: {exc}.", approval=card)
    row = session.get(Approval, approval_id)
    assert row is not None
    card = approval_card(session, row)
    verb = "Approved" if decision == "approve" else "Rejected"
    return s.DecideOut(done=True, summary=f"{verb}: {card.title}.", approval=card)
