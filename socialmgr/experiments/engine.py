"""The experiment engine (MODEL.md §6, plan §7): protocols, approvals and slot registration.

- ``propose_experiment`` / ``schedule_experiment`` apply MODEL §6.9's permissions: per-post levers,
  up to 3 factors, π = 0.5 and horizons up to 4 weeks (8 when the owner proposes) start directly;
  agent proposals over 4 weeks, horizons over 8 weeks, a 4th
  concurrent experiment (or more than 3 per-post factors at once), levers outside the approved
  list, and cadence changes over ±1 post a day wait for the owner as a pending approval.
- ``amend_experiment`` writes a new protocol version; changes to a frozen field (metric, model, δ,
  horizon) once scheduled, excluding posts and overriding a guardrail become pending approvals.
- ``decide_approval`` requires ``actor="owner"``. The agent-facing services hard-code
  ``actor="agent"`` and never call it; only the CLI (``socialmgr approve``) and the web app do.
- ``register_slots`` registers whole local days up to 7 days ahead. For every slot it FIRST freezes
  the treatment-blind forecast (the protocol's frozen model; levers blinded) and only THEN draws
  the arms, with ``stats.assign_block`` on an RNG seeded by (experiment seed, block, factor).

The run step that watches guardrails, reads out and feeds e-LOND is in ``experiments/monitor.py``.
"""

import hashlib
import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr import plugins as plugin_registry
from socialmgr.core.config import Settings
from socialmgr.db.models import (
    Account,
    Approval,
    Assignment,
    Deviation,
    Experiment,
    Forecast,
    PlannedSlot,
    Post,
    ProtocolVersion,
)
from socialmgr.experiments import stats
from socialmgr.experiments.specs import (
    AGENT_AUTO_START_MAX_WEEKS,
    AUTO_START_MAX_WEEKS,
    LEVERS,
    MAX_CONCURRENT,
    MAX_DAYS_AHEAD,
    MAX_FACTORS,
    SWITCHBACK_BLOCK_DAYS,
    SWITCHBACK_WASHOUT_DAYS,
    Actor,
    DayPlan,
    ExperimentSpec,
    FactorSpec,
    SlotSpec,
)
from socialmgr.experiments.suggestions import BY_KEY, SUGGESTIONS, Suggestion
from socialmgr.services import pipeline
from socialmgr.services.hashing import sha256_json
from socialmgr.services.planned import MIN_ORIGINAL_SPACING
from socialmgr.services.plugins import ActivePlugins

PROPENSITY = 0.5
CONCURRENT_STATUSES = ("scheduled", "active", "paused")  # count toward MODEL §6.9's limit of 3
DRAWING_STATUSES = ("scheduled", "active")  # receive new draws (a paused experiment doesn't)
OPEN_STATUSES = (*CONCURRENT_STATUSES, "pending_approval")
TERMINAL_STATUSES = ("concluded", "stopped")
SPACED_TYPES = ("original", "quote")
RATE_WINDOW = timedelta(days=28)
REVERT_KINDS = ("reverted_after_pause", "reverted_after_stop")
# MODEL §6.8: switchback power at 1.5x / 2x for 8, 12 and 16 weeks (4-day blocks).
SWITCHBACK_POWER = {8: (0.20, 0.46), 12: (0.29, 0.65), 16: (0.37, 0.78)}


class ExperimentError(ValueError):
    """A request the engine refuses (invalid, or against a hard rule)."""


class ApprovalForbidden(PermissionError):
    """Only the owner, in the web app or the CLI, can decide an approval (MODEL §6.9)."""


# ---------------------------------------------------------------- lookups and time


def get_experiment(session: Session, settings: Settings, ref: str | int) -> Experiment:
    """By key (``E03``), record id (``exp:7``) or numeric id."""
    raw = str(ref).strip()
    exp: Experiment | None = None
    if raw.startswith("exp:"):
        raw = raw[4:]
    if raw.isdigit():
        exp = session.get(Experiment, int(raw))
    if exp is None:
        exp = session.scalar(
            select(Experiment).where(Experiment.workspace_id == settings.workspace_id, Experiment.key == raw)
        )
    if exp is None or exp.workspace_id != settings.workspace_id:
        raise ExperimentError(f"no experiment {ref!r}")
    return exp


def account_tz(session: Session, settings: Settings) -> ZoneInfo:
    owner = pipeline.owner_account(session, settings.workspace_id)
    return ZoneInfo(owner.tz if owner is not None else settings.account.tz)


def local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)


def start_day(exp: Experiment, tz: ZoneInfo) -> date | None:
    return exp.started_at.astimezone(tz).date() if exp.started_at is not None else None


def horizon_end(exp: Experiment) -> datetime | None:
    if exp.started_at is None:
        return None
    return exp.started_at + timedelta(days=7 * exp.horizon_weeks)


def factors_of(exp: Experiment) -> list[FactorSpec]:
    return [FactorSpec.model_validate(f) for f in exp.factors]


def _owner(session: Session, settings: Settings, now: datetime) -> Account:
    owner = pipeline.owner_account(session, settings.workspace_id)
    return owner if owner is not None else pipeline.ensure_accounts(session, settings, now)[0]


def _h32(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")


def block_rng(seed: int, block_key: str, factor: str) -> np.random.Generator:
    """The seeded RNG for one factor in one block: draws reproduce from the protocol seed alone."""
    return np.random.default_rng([seed, _h32(block_key), _h32(factor)])


# ---------------------------------------------------------------- power (MODEL §6.8)


@dataclass(frozen=True)
class PowerReport:
    design: str
    posts_per_day: float
    n_posts: int  # per-post: analysed posts; switchback: 4-day blocks
    power: dict[str, float | None]  # "1.25x", "1.5x", "2x"
    mde_ratio_80: float | None
    note: str


def posting_rate(session: Session, settings: Settings, now: datetime, applies: Any = None) -> float:
    """Owner originals a day over the last 4 weeks (minimum 1/day). With a factor filter, the share
    of those originals whose labels match it, still at least 1/day."""
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        return 1.0
    posts = session.scalars(
        select(Post).where(
            Post.account_id == owner.id,
            Post.type == "original",
            Post.deleted_at.is_(None),
            Post.created_at > now - RATE_WINDOW,
            Post.created_at <= now,
        )
    ).all()
    n = len(posts)
    if applies is not None and (applies.labels or applies.labels_not):
        n = sum(1 for p in posts if applies.matches(p.type, pipeline.effective_labels(session, p.id)))
    return max(1.0, n / RATE_WINDOW.days)


def power_for(spec: ExperimentSpec, posts_per_day: float) -> PowerReport:
    if spec.design == "switchback":
        weeks = min(SWITCHBACK_POWER, key=lambda w: abs(w - spec.horizon_weeks))
        p15, p2 = SWITCHBACK_POWER[weeks]
        return PowerReport(
            design="switchback",
            posts_per_day=posts_per_day,
            n_posts=int(spec.horizon_weeks * 7 // SWITCHBACK_BLOCK_DAYS),
            power={"1.25x": None, "1.5x": p15, "2x": p2},
            mde_ratio_80=None,
            note=(
                f"Switchback power from MODEL §6.8's simulation at {weeks} weeks (4-day blocks); "
                "1.25x is not detectable."
            ),
        )
    n = max(1, round(posts_per_day * 7 * spec.horizon_weeks))
    pv = stats.power_preview(n)
    return PowerReport(
        design="per_post",
        posts_per_day=round(posts_per_day, 2),
        n_posts=n,
        power={k: v for k, v in pv.power.items()},
        mde_ratio_80=pv.mde_ratio_80,
        note=(
            f"{n} posts over {spec.horizon_weeks:g} weeks at {posts_per_day:.1f}/day; "
            "SE = 2 sigma sqrt(1 - R^2) / sqrt(n) with sigma = 1.1, R^2 = 0.3 (MODEL §6.8)."
        ),
    )


def power_check(session: Session, settings: Settings, spec: ExperimentSpec, now: datetime) -> PowerReport:
    """The three-number preview for this account's posting rate (the rarest factor's posts)."""
    rate = min(posting_rate(session, settings, now, f.applies_to) for f in spec.factors)
    return power_for(spec, rate)


@dataclass(frozen=True)
class SuggestionView:
    suggestion: Suggestion
    power: PowerReport
    already_running: bool


def suggestions(session: Session, settings: Settings, now: datetime) -> list[SuggestionView]:
    """E01-E09 with a live power preview for this account."""
    open_keys = set(
        session.scalars(
            select(Experiment.suggestion_key).where(
                Experiment.workspace_id == settings.workspace_id, Experiment.status.in_(OPEN_STATUSES)
            )
        )
    )
    return [
        SuggestionView(s, power_check(session, settings, s.spec, now), s.key in open_keys)
        for s in SUGGESTIONS
    ]


# ---------------------------------------------------------------- protocols


def protocol_of(exp: Experiment, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    proto = {
        "key": exp.key,
        "name": exp.name,
        "hypothesis": exp.hypothesis,
        "design": exp.design,
        "factors": exp.factors,
        "primary_metric": exp.primary_metric,
        "delta": exp.delta,
        "horizon_weeks": exp.horizon_weeks,
        "model_plugin": exp.model_plugin,
        "model_version": exp.model_version,
        "seed": exp.seed,
        "propensity": PROPENSITY,
        "extension_used": exp.extension_used,
    }
    return {**proto, **(extra or {})}


def latest_protocol(session: Session, exp: Experiment) -> ProtocolVersion | None:
    return session.scalar(
        select(ProtocolVersion)
        .where(ProtocolVersion.experiment_id == exp.id)
        .order_by(ProtocolVersion.version.desc())
        .limit(1)
    )


def _write_protocol(
    session: Session, exp: Experiment, actor: str, reason: str, now: datetime, extra: Mapping[str, Any] | None
) -> ProtocolVersion:
    prev = latest_protocol(session, exp)
    carried = {"secondary_metrics": prev.protocol.get("secondary_metrics", [])} if prev else {}
    proto = protocol_of(exp, {**carried, **(extra or {})})
    version = (prev.version + 1) if prev else 1
    exp.protocol_version = version
    exp.updated_at = now
    row = ProtocolVersion(
        workspace_id=exp.workspace_id,
        experiment_id=exp.id,
        version=version,
        protocol=proto,
        hash=sha256_json(proto),
        created_at=now,
        created_by=actor,
        reason=reason,
    )
    session.add(row)
    session.flush()
    return row


def frozen_model_name(settings: Settings, plugins: ActivePlugins) -> tuple[str, str]:
    """(registry name, version tag) of the model a new protocol freezes: the current champion."""
    m, p = plugins.champion, plugins.prior_source
    return settings.plugins.champion_model, f"{m.family}@{m.version}+{p.name}@{p.version}"


def frozen_plugins(settings: Settings, plugins: ActivePlugins, exp: Experiment) -> ActivePlugins:
    """Plugins whose champion is the protocol's frozen model, so blind forecasts keep one scale."""
    model = plugin_registry.models.resolve(exp.model_plugin, settings)
    p = plugins.prior_source
    tag = f"{model.family}@{model.version}+{p.name}@{p.version}"
    if tag != exp.model_version:
        raise ExperimentError(
            f"{exp.key}'s frozen model {exp.model_version} is no longer available (this install has {tag}). "
            "Amending the frozen model needs the owner's approval."
        )
    return replace(plugins, champion=model)


# ---------------------------------------------------------------- approvals


def _approval(
    session: Session,
    exp: Experiment | None,
    kind: str,
    payload: dict[str, Any],
    reasons: list[str],
    actor: str,
    now: datetime,
) -> Approval:
    row = Approval(
        workspace_id=exp.workspace_id if exp is not None else 1,
        experiment_id=exp.id if exp is not None else None,
        kind=kind,
        payload=payload,
        reasons=reasons,
        requested_by=actor,
        requested_at=now,
        status="pending",
    )
    session.add(row)
    session.flush()
    return row


def list_approvals(session: Session, settings: Settings, status: str | None = "pending") -> list[Approval]:
    q = select(Approval).where(Approval.workspace_id == settings.workspace_id)
    if status is not None:
        q = q.where(Approval.status == status)
    return list(session.scalars(q.order_by(Approval.id)))


def _concurrent(session: Session, settings: Settings, exclude_id: int | None = None) -> list[Experiment]:
    rows = session.scalars(
        select(Experiment).where(
            Experiment.workspace_id == settings.workspace_id, Experiment.status.in_(CONCURRENT_STATUSES)
        )
    ).all()
    return [e for e in rows if e.id != exclude_id]


def approval_reasons(session: Session, settings: Settings, exp: Experiment) -> list[str]:
    """MODEL §6.9 items this experiment needs the owner for (empty: it may start directly)."""
    reasons = []
    if exp.horizon_weeks > AUTO_START_MAX_WEEKS:
        reasons.append(f"horizon of {exp.horizon_weeks:g} weeks is over 8 weeks")
    elif exp.created_by == "agent" and exp.horizon_weeks > AGENT_AUTO_START_MAX_WEEKS:
        reasons.append(f"an agent proposed a {exp.horizon_weeks:g}-week horizon (agents may start up to 4)")
    others = _concurrent(session, settings, exclude_id=exp.id)
    if len(others) >= MAX_CONCURRENT:
        reasons.append(f"it would be experiment {len(others) + 1} running at once (more than 3)")
    if exp.design == "per_post":
        n_factors = len(exp.factors) + sum(len(e.factors) for e in others if e.design == "per_post")
        if n_factors > MAX_FACTORS:
            reasons.append(f"{n_factors} per-post factors at once (the factorial holds 3)")
    for f in factors_of(exp):
        if f.lever not in LEVERS:
            reasons.append(f"lever {f.lever!r} is outside the approved per-post list")
        if f.lever == "frequency" and abs(int(f.treatment) - int(f.control)) > 1:
            reasons.append("changes cadence by more than ±1 post a day")
    return reasons


def _hard_checks(session: Session, settings: Settings, exp: Experiment) -> None:
    if exp.design == "switchback":
        others = [
            e
            for e in session.scalars(
                select(Experiment).where(
                    Experiment.workspace_id == settings.workspace_id, Experiment.status.in_(OPEN_STATUSES)
                )
            )
            if e.id != exp.id and e.design == "switchback"
        ]
        if others:
            raise ExperimentError(
                f"{others[0].key} is already a time-level experiment: at most one switchback runs at a time"
            )


# ---------------------------------------------------------------- propose / schedule


@dataclass
class ProposeResult:
    experiment: Experiment
    power: PowerReport
    approval: Approval | None
    reasons: list[str] = field(default_factory=list)


def _unique_key(session: Session, settings: Settings, base: str) -> str:
    taken = set(
        session.scalars(select(Experiment.key).where(Experiment.workspace_id == settings.workspace_id))
    )
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def propose_experiment(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    *,
    actor: Actor,
    now: datetime,
    suggestion_key: str | None = None,
    spec: ExperimentSpec | None = None,
    start: bool = True,
) -> ProposeResult:
    """Create an experiment from a suggestion (E01-E09) or a custom spec and, with ``start``,
    schedule it: directly when MODEL §6.9 allows, else as a pending approval."""
    if (suggestion_key is None) == (spec is None):
        raise ExperimentError("give either a suggestion key or a custom spec")
    if suggestion_key is not None:
        sug = BY_KEY.get(suggestion_key.upper())
        if sug is None:
            raise ExperimentError(f"unknown suggestion {suggestion_key!r}; choose one of E01-E09")
        spec = sug.spec
    assert spec is not None
    owner = _owner(session, settings, now)
    key = _unique_key(session, settings, spec.key or f"X{len(list_all(session, settings)) + 1:02d}")
    model_plugin, model_version = frozen_model_name(settings, plugins)
    seed = spec.seed if spec.seed is not None else _h32(f"{key}|{now.isoformat()}") % (2**31)
    exp = Experiment(
        workspace_id=settings.workspace_id,
        account_id=owner.id,
        key=key,
        suggestion_key=suggestion_key.upper() if suggestion_key else None,
        name=spec.name,
        hypothesis=spec.hypothesis,
        design=spec.design,
        factors=[f.model_dump(mode="json") for f in spec.factors],
        primary_metric=spec.primary_metric,
        delta=spec.delta,
        horizon_weeks=spec.horizon_weeks,
        status="draft",
        protocol_version=1,
        model_plugin=model_plugin,
        model_version=model_version,
        seed=seed,
        created_by=actor,
        created_at=now,
        updated_at=now,
        extension_used=False,
        paused_arms={},
        decisions={},
    )
    session.add(exp)
    session.flush()
    _write_protocol(
        session,
        exp,
        actor,
        f"proposed from {suggestion_key.upper()}" if suggestion_key else "proposed",
        now,
        {"secondary_metrics": spec.secondary_metrics},
    )
    power = power_check(session, settings, spec, now)
    approval, reasons = None, []
    if start:
        sched = schedule_experiment(session, settings, exp.key, actor=actor, now=now)
        approval, reasons = sched.approval, sched.reasons
    return ProposeResult(exp, power, approval, reasons)


@dataclass
class ScheduleResult:
    experiment: Experiment
    approval: Approval | None
    reasons: list[str]


def schedule_experiment(
    session: Session, settings: Settings, ref: str | int, *, actor: Actor, now: datetime
) -> ScheduleResult:
    """Draft -> scheduled (or pending_approval). Slots registered afterwards draw its arms."""
    exp = get_experiment(session, settings, ref)
    if exp.status != "draft":
        raise ExperimentError(f"{exp.key} is {exp.status}; only a draft can be scheduled")
    _hard_checks(session, settings, exp)
    reasons = approval_reasons(session, settings, exp)
    if reasons:
        exp.status = "pending_approval"
        exp.updated_at = now
        approval = _approval(session, exp, "start_experiment", {"key": exp.key}, reasons, actor, now)
        return ScheduleResult(exp, approval, reasons)
    exp.status = "scheduled"
    exp.scheduled_at = now
    exp.updated_at = now
    session.flush()
    return ScheduleResult(exp, None, [])


def list_all(session: Session, settings: Settings, status: Sequence[str] | None = None) -> list[Experiment]:
    q = select(Experiment).where(Experiment.workspace_id == settings.workspace_id)
    if status:
        q = q.where(Experiment.status.in_(tuple(status)))
    return list(session.scalars(q.order_by(Experiment.id)))


# ---------------------------------------------------------------- amend / extend / stop


class AmendRequest(BaseModel):
    """Protocol changes. Free fields apply at once (a new protocol version); frozen fields, post
    exclusions and guardrail overrides become pending approvals once the experiment is scheduled."""

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    hypothesis: str | None = None
    secondary_metrics: list[str] | None = None
    primary_metric: str | None = None  # frozen
    delta: float | None = None  # frozen
    horizon_weeks: float | None = Field(default=None, gt=0, le=52)  # frozen once scheduled
    refreeze_model: bool = False  # frozen: re-freeze on the current champion
    factors: list[FactorSpec] | None = None  # drafts only
    exclude_slots: list[int] = Field(default_factory=list)  # needs the owner
    resume_factors: list[str] = Field(default_factory=list)  # guardrail override: needs the owner
    resume_experiment: bool = False  # guardrail override: needs the owner


@dataclass
class AmendResult:
    experiment: Experiment
    applied: list[str]
    protocol_version: int
    approvals: list[Approval]


FROZEN_FIELDS = ("primary_metric", "delta", "horizon_weeks", "refreeze_model")


def _apply_changes(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins | None,
    exp: Experiment,
    changes: Mapping[str, Any],
) -> list[str]:
    applied = []
    for name in ("name", "hypothesis", "primary_metric", "delta", "horizon_weeks"):
        if changes.get(name) is not None:
            setattr(exp, name, changes[name])
            applied.append(name)
    if changes.get("factors") is not None:
        exp.factors = [FactorSpec.model_validate(f).model_dump(mode="json") for f in changes["factors"]]
        applied.append("factors")
    if changes.get("refreeze_model"):
        if plugins is None:
            raise ExperimentError("re-freezing the model needs the active plugins")
        exp.model_plugin, exp.model_version = frozen_model_name(settings, plugins)
        applied.append("model")
    # Re-validate the protocol as a whole.
    ExperimentSpec.model_validate(
        {
            "key": exp.key,
            "name": exp.name,
            "hypothesis": exp.hypothesis,
            "design": exp.design,
            "factors": exp.factors,
            "primary_metric": exp.primary_metric,
            "delta": exp.delta,
            "horizon_weeks": exp.horizon_weeks,
        }
    )
    return applied


def amend_experiment(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins | None,
    ref: str | int,
    changes: AmendRequest,
    *,
    actor: Actor,
    reason: str,
    now: datetime,
) -> AmendResult:
    exp = get_experiment(session, settings, ref)
    if exp.status in TERMINAL_STATUSES:
        raise ExperimentError(f"{exp.key} is {exp.status}; propose a new experiment instead")
    draft = exp.status == "draft"
    if changes.factors is not None and not draft:
        raise ExperimentError(
            "factors can't change once an experiment is scheduled: stop it and propose anew"
        )
    free = {k: getattr(changes, k) for k in ("name", "hypothesis") if getattr(changes, k) is not None}
    frozen = {k: getattr(changes, k) for k in FROZEN_FIELDS if getattr(changes, k) not in (None, False)}
    if draft:
        free.update(frozen)
        frozen = {}
        if changes.factors is not None:
            free["factors"] = [f.model_dump(mode="json") for f in changes.factors]
    approvals: list[Approval] = []
    applied: list[str] = []
    extra: dict[str, Any] = {}
    if changes.secondary_metrics is not None:
        extra["secondary_metrics"] = changes.secondary_metrics
        applied.append("secondary_metrics")
    if free:
        applied += _apply_changes(session, settings, plugins, exp, free)
    if applied:
        _write_protocol(session, exp, actor, reason or "amended", now, extra)
    if frozen:
        approvals.append(
            _approval(
                session,
                exp,
                "amend_protocol",
                {"changes": frozen, "reason": reason},
                [f"changes the frozen {', '.join(sorted(frozen))}"],
                actor,
                now,
            )
        )
    if changes.exclude_slots:
        approvals.append(
            _approval(
                session,
                exp,
                "exclude_posts",
                {"slot_ids": sorted(set(changes.exclude_slots)), "reason": reason},
                [f"excludes {len(set(changes.exclude_slots))} slot(s) from the analysis"],
                actor,
                now,
            )
        )
    if changes.resume_factors or changes.resume_experiment:
        approvals.append(
            _approval(
                session,
                exp,
                "guardrail_override",
                {
                    "factors": sorted(set(changes.resume_factors)),
                    "experiment": changes.resume_experiment,
                    "reason": reason,
                },
                ["overrides a guardrail pause"],
                actor,
                now,
            )
        )
    session.flush()
    return AmendResult(exp, applied, exp.protocol_version, approvals)


def extend_experiment(
    session: Session,
    settings: Settings,
    ref: str | int,
    *,
    actor: Actor,
    now: datetime,
    fraction: float = 0.5,
) -> AmendResult:
    """MODEL §6.3: one extension of up to 50%, which the agent may start on its own (§6.9)."""
    exp = get_experiment(session, settings, ref)
    if exp.status not in ("active", "paused"):
        raise ExperimentError(f"{exp.key} is {exp.status}; only a running experiment can be extended")
    if exp.extension_used:
        raise ExperimentError(f"{exp.key} has already used its one extension")
    if not 0 < fraction <= 0.5:
        raise ExperimentError("an extension is at most 50% of the horizon")
    new_weeks = math.ceil(exp.horizon_weeks * (1 + fraction) * 7) / 7
    if new_weeks > AUTO_START_MAX_WEEKS:
        approval = _approval(
            session,
            exp,
            "amend_protocol",
            {"changes": {"horizon_weeks": new_weeks, "extension": True}, "reason": "extension"},
            [f"the extension takes the horizon to {new_weeks:g} weeks (over 8)"],
            actor,
            now,
        )
        return AmendResult(exp, [], exp.protocol_version, [approval])
    exp.horizon_weeks = new_weeks
    exp.extension_used = True
    exp.extension_offered_at = None
    _write_protocol(session, exp, actor, f"extended by {fraction:.0%} to {new_weeks:g} weeks", now, None)
    return AmendResult(exp, ["horizon_weeks"], exp.protocol_version, [])


def _future_planned_slots(session: Session, exp: Experiment, now: datetime) -> list[PlannedSlot]:
    return list(
        session.scalars(
            select(PlannedSlot)
            .join(Assignment, Assignment.slot_id == PlannedSlot.id)
            .where(
                Assignment.experiment_id == exp.id,
                PlannedSlot.state == "planned",
                PlannedSlot.planned_at > now,
            )
            .distinct()
        )
    )


def revert_to_control(
    session: Session,
    exp: Experiment,
    factor: str | None,
    kind: str,
    detail: str,
    now: datetime,
    run_id: int | None = None,
) -> int:
    """Slots already drawn treated (for one factor, or every factor) that aren't posted yet revert
    to control, each logged as a deviation. The readout stays ITT on the original assignment."""
    reverted = 0
    for slot in _future_planned_slots(session, exp, now):
        rows = session.scalars(
            select(Assignment).where(
                Assignment.experiment_id == exp.id, Assignment.slot_id == slot.id, Assignment.arm == 1
            )
        ).all()
        for a in rows:
            if factor is not None and a.factor != factor:
                continue
            if record_deviation(session, exp, slot.id, a.factor, kind, detail, now, run_id):
                ctx = dict(slot.lever_context or {})
                exps = dict(ctx.get("experiments", {}))
                entry = dict(exps.get(exp.key, {}))
                f = dict(entry.get(a.factor, {}))
                spec = next((s for s in factors_of(exp) if s.name == a.factor), None)
                f.update({"arm": 0, "value": spec.control if spec else None, "reverted": kind})
                entry[a.factor] = f
                exps[exp.key] = entry
                ctx["experiments"] = exps
                slot.lever_context = ctx
                reverted += 1
    session.flush()
    return reverted


def record_deviation(
    session: Session,
    exp: Experiment,
    slot_id: int | None,
    factor: str,
    kind: str,
    detail: str,
    now: datetime,
    run_id: int | None = None,
) -> bool:
    """Log a deviation once (idempotent on resume). Returns whether it was new."""
    exists = session.scalar(
        select(Deviation.id).where(
            Deviation.experiment_id == exp.id,
            Deviation.slot_id.is_(None) if slot_id is None else Deviation.slot_id == slot_id,
            Deviation.factor == factor,
            Deviation.kind == kind,
        )
    )
    if exists is not None:
        return False
    session.add(
        Deviation(
            workspace_id=exp.workspace_id,
            experiment_id=exp.id,
            slot_id=slot_id,
            factor=factor,
            kind=kind,
            detail=detail,
            run_id=run_id,
            created_at=now,
        )
    )
    session.flush()
    return True


def stop_experiment(
    session: Session, settings: Settings, ref: str | int, *, actor: Actor, reason: str, now: datetime
) -> Experiment:
    """Stop for good: no more draws or readouts and no e-value (an early stop isn't confirmatory)."""
    exp = get_experiment(session, settings, ref)
    if exp.status in TERMINAL_STATUSES:
        raise ExperimentError(f"{exp.key} is already {exp.status}")
    revert_to_control(session, exp, None, "reverted_after_stop", f"stopped by {actor}: {reason}", now)
    for a in session.scalars(
        select(Approval).where(Approval.experiment_id == exp.id, Approval.status == "pending")
    ):
        a.status, a.decided_by, a.decided_at, a.note = "rejected", "system", now, "experiment stopped"
    exp.status = "stopped"
    exp.concluded_at = now
    exp.outcome = f"Stopped by {actor}: {reason}"
    _write_protocol(session, exp, actor, f"stopped: {reason}", now, {"status": "stopped"})
    return exp


# ---------------------------------------------------------------- decide approvals (owner only)


def decide_approval(
    session: Session,
    settings: Settings,
    approval_id: int,
    decision: str,
    *,
    actor: str,
    now: datetime,
    note: str | None = None,
    plugins: ActivePlugins | None = None,
) -> Approval:
    """Approve or reject a pending item. Only ``actor="owner"`` (the web app, ``socialmgr approve``)."""
    if actor != "owner":
        raise ApprovalForbidden(
            "approvals are decided by the owner in the web app or the CLI, never by an agent"
        )
    if decision not in ("approve", "reject"):
        raise ExperimentError("decision is approve or reject")
    row = session.get(Approval, approval_id)
    if row is None or row.workspace_id != settings.workspace_id:
        raise ExperimentError(f"no approval {approval_id}")
    if row.status != "pending":
        raise ExperimentError(f"approval {approval_id} is already {row.status}")
    exp = session.get(Experiment, row.experiment_id) if row.experiment_id is not None else None
    row.status = "approved" if decision == "approve" else "rejected"
    row.decided_by, row.decided_at, row.note = actor, now, note
    if exp is None:
        session.flush()
        return row
    if decision == "reject":
        if row.kind == "start_experiment" and exp.status == "pending_approval":
            exp.status = "draft"
            exp.updated_at = now
        session.flush()
        return row
    if row.kind == "start_experiment":
        if exp.status != "pending_approval":
            raise ExperimentError(f"{exp.key} is {exp.status}, not waiting to start")
        _hard_checks(session, settings, exp)
        exp.status = "scheduled"
        exp.scheduled_at = now
        exp.updated_at = now
        _write_protocol(session, exp, actor, f"start approved (approval {row.id})", now, None)
    elif row.kind == "amend_protocol":
        changes = dict(row.payload.get("changes", {}))
        extension = bool(changes.pop("extension", False))
        _apply_changes(session, settings, plugins, exp, changes)
        if extension:
            exp.extension_used = True
            exp.extension_offered_at = None
        _write_protocol(session, exp, actor, f"amendment approved (approval {row.id})", now, None)
    elif row.kind == "exclude_posts":
        for slot_id in row.payload.get("slot_ids", []):
            record_deviation(session, exp, int(slot_id), "", "excluded", f"approval {row.id}", now)
        _write_protocol(
            session,
            exp,
            actor,
            f"exclusions approved (approval {row.id})",
            now,
            {"excluded_slots": row.payload.get("slot_ids", [])},
        )
    elif row.kind == "guardrail_override":
        paused = dict(exp.paused_arms or {})
        for f in row.payload.get("factors", []):
            paused.pop(f, None)
        exp.paused_arms = paused
        if row.payload.get("experiment") and exp.status == "paused":
            exp.status = "active"
            exp.pause_reason = None
        _write_protocol(session, exp, actor, f"guardrail override approved (approval {row.id})", now, None)
    session.flush()
    return row


# ---------------------------------------------------------------- slot registration


@dataclass(frozen=True)
class SlotArm:
    experiment: str
    factor: str
    lever: str
    arm: int
    value: str
    block_key: str
    measured: bool


@dataclass
class RegisteredSlot:
    slot: PlannedSlot
    blind_forecast: Forecast
    arms: list[SlotArm]
    cancelled: bool = False  # a switchback candidate the day's condition didn't pick


@dataclass
class RegisterResult:
    slots: list[RegisteredSlot]
    experiments: list[str]
    warnings: list[str] = field(default_factory=list)


def _spacing_errors(day: date, specs: Sequence[SlotSpec]) -> list[str]:
    times = sorted(datetime.combine(day, s.time) for s in specs if s.post_type in SPACED_TYPES)
    return [
        f"{a:%H:%M} and {b:%H:%M} on {day} are less than 3h apart (MODEL §6.6)"
        for a, b in itertools.pairwise(times)
        if b - a < MIN_ORIGINAL_SPACING
    ]


def _blinded_fields(exps: Sequence[Experiment]) -> set[str]:
    fields: set[str] = set()
    for e in exps:
        for f in factors_of(e):
            lever = LEVERS.get(f.lever)
            fields |= set(lever.blinds) if lever else {f.lever}
            fields.add(f.lever)
    return fields


def blind_manifest(
    session: Session, settings: Settings, slot: PlannedSlot, blinded: set[str], keys: list[str], now: datetime
) -> dict[str, Any]:
    """The live manifest of a slot with every randomized lever's fields removed (treatment-blind)."""
    labels = {k: v for k, v in (slot.labels or {}).items() if k not in blinded}
    view = PlannedSlot(
        id=slot.id,
        workspace_id=slot.workspace_id,
        account_id=slot.account_id,
        planned_at=slot.planned_at,
        post_type=slot.post_type,
        labels=labels,
        state=slot.state,
        registered_by=slot.registered_by,
        created_at=slot.created_at,
    )
    manifest = pipeline.build_live_manifest(session, settings.workspace_id, view, now)
    manifest["treatment_blind"] = {"blinded_fields": sorted(blinded), "experiments": sorted(keys)}
    return manifest


def _day_slots_registered(session: Session, owner_id: int, day: date, tz: ZoneInfo) -> bool:
    lo, hi = local_midnight(day, tz), local_midnight(day + timedelta(days=1), tz)
    return (
        session.scalar(
            select(func.count(PlannedSlot.id)).where(
                PlannedSlot.account_id == owner_id,
                PlannedSlot.experiment_id.is_not(None),
                PlannedSlot.state != "cancelled",
                PlannedSlot.planned_at >= lo,
                PlannedSlot.planned_at < hi,
            )
        )
        or 0
    ) > 0


def _existing_conflicts(session: Session, owner_id: int, when: datetime) -> list[str]:
    lo, hi = when - MIN_ORIGINAL_SPACING, when + MIN_ORIGINAL_SPACING
    posts = session.scalars(
        select(Post.id).where(
            Post.account_id == owner_id,
            Post.type.in_(SPACED_TYPES),
            Post.created_at > lo,
            Post.created_at < hi,
        )
    ).all()
    slots = session.scalars(
        select(PlannedSlot.id).where(
            PlannedSlot.account_id == owner_id,
            PlannedSlot.state == "planned",
            PlannedSlot.post_type.in_(SPACED_TYPES),
            PlannedSlot.planned_at > lo,
            PlannedSlot.planned_at < hi,
        )
    ).all()
    return [f"post:{i}" for i in posts] + [f"slot:{i}" for i in slots]


def switchback_condition(exp: Experiment, day: date, tz: ZoneInfo) -> tuple[int, str, bool]:
    """(arm, block key, measured) of a day in a switchback: 4-day blocks paired from the start, one
    block of each pair treated (``assign_block`` on the pair), washout days not measured."""
    first = start_day(exp, tz)
    assert first is not None
    offset = (day - first).days
    block = offset // SWITCHBACK_BLOCK_DAYS
    pair = block // 2
    factor = factors_of(exp)[0].name
    keys = [f"b{2 * pair}", f"b{2 * pair + 1}"]
    arms = stats.assign_block(keys, [factor], block_rng(exp.seed, f"sb:{pair}", factor))
    measured = offset % SWITCHBACK_BLOCK_DAYS >= SWITCHBACK_WASHOUT_DAYS
    return arms[f"b{block}"][factor], f"sb:{pair}:b{block}", measured


def _slot_key(slot: PlannedSlot, tz: ZoneInfo) -> str:
    return slot.planned_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M")


def draw_per_post(
    exp: Experiment,
    by_day: Mapping[date, Sequence[tuple[str, str, Mapping[str, str]]]],
    tz: ZoneInfo,
    day_blocks_only: bool,
    skip_factors: frozenset[str] = frozenset(),
) -> dict[str, dict[str, tuple[int, str]]]:
    """Seeded blocked draws for one per-post experiment over whole days.

    ``by_day`` maps each local day to its kept slots as (slot key, post type, labels). Each factor
    is drawn per block (the local day; at one applicable slot a day, the 2-day pair counted from
    the experiment's start when both days are in this call). Returns {slot key: {factor: (arm, block)}}.
    """
    first = start_day(exp, tz)
    assert first is not None
    out: dict[str, dict[str, tuple[int, str]]] = {}
    for f in factors_of(exp):
        if f.name in skip_factors:
            continue
        applicable = {
            d: [k for k, ptype, labels in slots if f.applies_to.matches(ptype, dict(labels))]
            for d, slots in by_day.items()
        }
        done: set[date] = set()
        for d in sorted(applicable):
            if d in done or not applicable[d]:
                continue
            offset = (d - first).days
            partner = first + timedelta(days=offset - 1 if offset % 2 else offset + 1)
            if (
                not day_blocks_only
                and len(applicable[d]) == 1
                and len(applicable.get(partner, [])) == 1
                and partner not in done
            ):
                d0, d1 = sorted((d, partner))
                block, keys = f"p:{d0}/{d1}", sorted(applicable[d0] + applicable[d1])
                done |= {d0, d1}
            else:
                block, keys = f"d:{d}", sorted(applicable[d])
                done.add(d)
            arms = stats.assign_block(keys, [f.name], block_rng(exp.seed, block, f.name))
            for k in keys:
                out.setdefault(k, {})[f.name] = (arms[k][f.name], block)
    return out


def register_slots(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    plans: Sequence[DayPlan],
    *,
    now: datetime,
    actor: Actor,
    experiment_keys: Sequence[str] | None = None,
) -> RegisterResult:
    """Register whole local days of slots for every scheduled or active experiment (a factorial
    across concurrent per-post experiments; at most one switchback picks the day's slots).

    Order per call: create the slots, freeze each slot's treatment-blind forecast, THEN draw.
    """
    if not plans:
        raise ExperimentError("no days given")
    owner = _owner(session, settings, now)
    tz = ZoneInfo(owner.tz)
    today = now.astimezone(tz).date()
    exps = list_all(session, settings, DRAWING_STATUSES)
    if experiment_keys:
        wanted = {k.upper() for k in experiment_keys}
        exps = [e for e in exps if e.key.upper() in wanted]
        missing = wanted - {e.key.upper() for e in exps}
        if missing:
            raise ExperimentError(f"not scheduled or active: {', '.join(sorted(missing))}")
    if not exps:
        raise ExperimentError("no scheduled or active experiment to draw for: propose or schedule one first")
    days = sorted(p.day for p in plans)
    if len(set(days)) != len(days):
        raise ExperimentError("each day may appear once")
    errors: list[str] = []
    for p in plans:
        if p.day <= today:
            errors.append(f"{p.day} has started or passed: register whole days from tomorrow")
        elif p.day > today + timedelta(days=MAX_DAYS_AHEAD):
            errors.append(f"{p.day} is more than {MAX_DAYS_AHEAD} days ahead")
        if _day_slots_registered(session, owner.id, p.day, tz):
            errors.append(f"{p.day} already has experiment slots (whole days are registered once)")
        errors += _spacing_errors(p.day, p.slots)
        if p.treatment_slots:
            errors += _spacing_errors(p.day, p.treatment_slots)
    if errors:
        raise ExperimentError("; ".join(errors))

    # Experiments start on the first day registered for them; the horizon bounds later days.
    first_day = days[0]
    for e in exps:
        if e.started_at is None:
            e.started_at = local_midnight(first_day, tz)
    switchbacks = [e for e in exps if e.design == "switchback"]
    per_post = [e for e in exps if e.design == "per_post"]
    warnings: list[str] = []

    def runs_on(e: Experiment, d: date) -> bool:
        end = horizon_end(e)
        return end is not None and e.started_at is not None and e.started_at <= local_midnight(d, tz) < end

    idle = [p.day for p in plans if not any(runs_on(e, p.day) for e in exps)]
    if idle:
        warnings.append(f"no experiment runs on {', '.join(map(str, idle))}: no slots registered there")
    plans = [p for p in plans if p.day not in idle]
    if not plans:
        raise ExperimentError("every requested day is outside the experiments' horizons")
    days = sorted(p.day for p in plans)

    # 1. Candidate slots (a switchback day holds the union of both conditions' slots).
    planned: list[tuple[datetime, SlotSpec, date, bool, bool]] = []  # when, spec, day, in control, in treat
    for p in sorted(plans, key=lambda x: x.day):
        sb = next((e for e in switchbacks if runs_on(e, p.day)), None)
        treat = p.treatment_slots if (sb is not None and p.treatment_slots) else p.slots
        specs: dict[tuple[time, str], tuple[SlotSpec, bool, bool]] = {}
        for s in p.slots:
            specs[(s.time, s.post_type)] = (s, True, False)
        for s in treat:
            prev = specs.get((s.time, s.post_type))
            specs[(s.time, s.post_type)] = (prev[0] if prev else s, bool(prev and prev[1]), True)
        for (t, _ptype), (s, in_c, in_t) in sorted(specs.items(), key=lambda kv: kv[0][0]):
            planned.append((datetime.combine(p.day, t, tzinfo=tz).astimezone(UTC), s, p.day, in_c, in_t))
    # Spacing against posts and slots that already exist (this call's lists were checked above).
    for when, s, *_ in planned:
        conflicts = _existing_conflicts(session, owner.id, when) if s.post_type in SPACED_TYPES else []
        if conflicts:
            raise ExperimentError(
                f"{when:%Y-%m-%d %H:%M} UTC is within 3h of {', '.join(conflicts)} (MODEL §6.6)"
            )
    candidates: list[tuple[PlannedSlot, date, bool, bool]] = []  # slot, day, in control list, in treatment
    for when, s, day, in_c, in_t in planned:
        slot = PlannedSlot(
            workspace_id=settings.workspace_id,
            account_id=owner.id,
            planned_at=when,
            post_type=s.post_type,
            labels=dict(s.labels),
            lever_context=None,
            experiment_id=exps[0].id,
            state="planned",
            registered_by=actor,
            created_at=now,
        )
        session.add(slot)
        candidates.append((slot, day, in_c, in_t))
    session.flush()

    # 2. Treatment-blind forecasts, frozen before any arm is drawn.
    involved = {e.key: e for e in exps}
    blinded = _blinded_fields(exps)
    keys = sorted(involved)
    blind: dict[tuple[int, str], Forecast] = {}
    for slot, _d, _c, _t in candidates:
        for model_key in {e.model_plugin + "|" + e.model_version for e in exps}:
            e0 = next(e for e in exps if e.model_plugin + "|" + e.model_version == model_key)
            manifest = blind_manifest(session, settings, slot, blinded, keys, now)
            fc = pipeline.freeze_forecast(
                session,
                settings,
                frozen_plugins(settings, plugins, e0),
                f"slot:{slot.id}",
                "treatment_blind",
                manifest,
                now,
            )
            blind[(slot.id, model_key)] = fc
    last_frozen = max(fc.frozen_at for fc in blind.values())
    drawn_at = max(now, last_frozen) + timedelta(milliseconds=1)

    # 3. Draws.
    arms_by_slot: dict[int, list[SlotArm]] = {slot.id: [] for slot, *_ in candidates}
    cancelled: set[int] = set()
    rows: list[Assignment] = []
    sb_days: set[date] = set()
    for sb in switchbacks:
        f = factors_of(sb)[0]
        for slot, d, in_c, in_t in candidates:
            if not runs_on(sb, d):
                continue
            sb_days.add(d)
            arm, block, measured = switchback_condition(sb, d, tz)
            if (arm == 1 and not in_t) or (arm == 0 and not in_c):
                cancelled.add(slot.id)
                continue
            arms_by_slot[slot.id].append(
                SlotArm(sb.key, f.name, f.lever, arm, f.treatment if arm else f.control, block, measured)
            )
    for slot, *_ in candidates:
        if slot.id in cancelled:
            slot.state = "cancelled"
    for e in per_post:
        by_day: dict[date, list[tuple[str, str, Mapping[str, str]]]] = {}
        key_to_slot: dict[str, PlannedSlot] = {}
        for slot, d, _c, _t in candidates:
            if slot.id in cancelled or not runs_on(e, d):
                continue
            k = _slot_key(slot, tz)
            key_to_slot[k] = slot
            by_day.setdefault(d, []).append((k, slot.post_type, slot.labels or {}))
        skipped = [d for d in days if not runs_on(e, d)]
        if skipped:
            warnings.append(f"{e.key} doesn't run on {', '.join(map(str, skipped))} (outside its horizon)")
        drawn = draw_per_post(
            e, by_day, tz, day_blocks_only=bool(sb_days), skip_factors=frozenset(e.paused_arms or {})
        )
        specs_by_name = {f.name: f for f in factors_of(e)}
        for k, factor_arms in drawn.items():
            slot = key_to_slot[k]
            for fname, (arm, block) in factor_arms.items():
                f = specs_by_name[fname]
                arms_by_slot[slot.id].append(
                    SlotArm(e.key, fname, f.lever, arm, f.treatment if arm else f.control, block, True)
                )
    for slot, *_ in candidates:
        for sa in arms_by_slot[slot.id]:
            e = involved[sa.experiment]
            fc = blind[(slot.id, e.model_plugin + "|" + e.model_version)]
            rows.append(
                Assignment(
                    workspace_id=settings.workspace_id,
                    experiment_id=e.id,
                    slot_id=slot.id,
                    factor=sa.factor,
                    arm=sa.arm,
                    block_key=sa.block_key,
                    slot_key=_slot_key(slot, tz),
                    measured=sa.measured,
                    protocol_version=e.protocol_version,
                    drawn_at=drawn_at,
                    blind_forecast_id=fc.id,
                    blind_manifest_hash=fc.manifest_hash,
                    propensity=PROPENSITY,
                )
            )
        if arms_by_slot[slot.id]:
            ctx: dict[str, Any] = {"experiments": {}}
            for sa in arms_by_slot[slot.id]:
                ctx["experiments"].setdefault(sa.experiment, {})[sa.factor] = {
                    "lever": sa.lever,
                    "arm": sa.arm,
                    "value": sa.value,
                    "measured": sa.measured,
                }
            slot.lever_context = ctx
            slot.experiment_id = involved[arms_by_slot[slot.id][0].experiment].id
            slot.arm = ",".join(
                f"{sa.experiment}.{sa.factor}={'T' if sa.arm else 'C'}" for sa in arms_by_slot[slot.id]
            )[:32]
    session.add_all(rows)
    for e in exps:
        if e.status == "scheduled":
            e.status = "active"
            e.updated_at = now
    session.flush()
    return RegisterResult(
        slots=[
            RegisteredSlot(
                slot=slot,
                blind_forecast=next(fc for (sid, _m), fc in blind.items() if sid == slot.id),
                arms=arms_by_slot[slot.id],
                cancelled=slot.id in cancelled,
            )
            for slot, *_ in candidates
        ],
        experiments=keys,
        warnings=warnings,
    )


def day_plans(days: Sequence[date], times: Sequence[time], **slot: Any) -> list[DayPlan]:
    """The same slot times every day (a convenience for the CLI and simple agent calls)."""
    return [DayPlan(day=d, slots=[SlotSpec(time=t, **slot) for t in times]) for d in days]


# ---------------------------------------------------------------- reading assignments


def assignments_of(session: Session, exp: Experiment) -> list[Assignment]:
    return list(
        session.scalars(
            select(Assignment)
            .where(Assignment.experiment_id == exp.id)
            .order_by(Assignment.slot_id, Assignment.id)
        )
    )


def effective_arms(session: Session, slot_id: int) -> dict[str, int]:
    """What the owner should post for a slot: {"<key>.<factor>": arm}, after any reversion."""
    out: dict[str, int] = {}
    reverted = {
        (d.experiment_id, d.factor)
        for d in session.scalars(
            select(Deviation).where(Deviation.slot_id == slot_id, Deviation.kind.in_(REVERT_KINDS))
        )
    }
    for a, key in session.execute(
        select(Assignment, Experiment.key)
        .join(Experiment, Experiment.id == Assignment.experiment_id)
        .where(Assignment.slot_id == slot_id)
    ).all():
        out[f"{key}.{a.factor}"] = 0 if (a.experiment_id, a.factor) in reverted else a.arm
    return out


def verify_draws(session: Session, settings: Settings, exp: Experiment) -> list[str]:
    """Recompute every per-post draw from the protocol seed; returns mismatches (none expected)."""
    tz = account_tz(session, settings)
    rows = assignments_of(session, exp)
    problems = []
    if exp.design == "switchback":
        for a in rows:
            slot = session.get(PlannedSlot, a.slot_id)
            assert slot is not None
            arm, block, _m = switchback_condition(exp, slot.planned_at.astimezone(tz).date(), tz)
            if (arm, block) != (a.arm, a.block_key):
                problems.append(f"slot:{a.slot_id} {a.factor}")
        return problems
    by_block: dict[tuple[str, str], list[Assignment]] = {}
    for a in rows:
        by_block.setdefault((a.factor, a.block_key), []).append(a)
    for (factor, block), group in by_block.items():
        keys = [a.slot_key for a in sorted(group, key=lambda a: a.slot_key)]
        redraw = stats.assign_block(keys, [factor], block_rng(exp.seed, block, factor))
        for a in group:
            if redraw[a.slot_key][factor] != a.arm:
                problems.append(f"slot:{a.slot_id} {factor}")
    return problems
