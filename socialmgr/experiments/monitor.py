"""The "experiments" run step (MODEL.md §6.2-§6.4, §6.7), after scoring.

Every run: log deviations (missed slots, deleted posts), then the always-valid guardrails: arm
harm (a confidence sequence on treated-minus-control log(y/f), f the blind forecast median) pauses
an arm; the unfollow rate against the pre-experiment baseline pauses the experiment. Pausing stops
new draws, reverts drawn-but-unposted treated slots to control (deviations) and leaves the readout
intention-to-treat.

Readouts happen only at the first run after each experiment-week boundary, and the final read at
the first run after horizon + 48h (MODEL §6.3, batch-mode looks). A concluded experiment emits one
e-value per factor (``e_value_from_p`` of its randomization p) into the e-LOND stream; only e-LOND
rejections are labelled "confirmed". Every write is guarded so a resumed run doesn't repeat it.
"""

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from scipy import stats as sps
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import (
    Assignment,
    Deviation,
    ELondEntry,
    Experiment,
    FollowerSnapshot,
    Forecast,
    PlannedSlot,
    Post,
    Readout,
    Regime,
    Run,
    ScoreRow,
)
from socialmgr.experiments import engine, stats, stats_compat

FINAL_DELAY = timedelta(hours=48)
MATURITY_WAIT = timedelta(days=7)  # the final read waits this long at most for 48h captures
EXTENSION_WINDOW = timedelta(days=7)  # time to take the offered extension before concluding
UNFOLLOW_THRESHOLD = math.log(1.25)  # pause when unfollows run clearly 25%+ above the baseline
UNFOLLOW_BASELINE = timedelta(days=28)
ELOND_ALPHA = 0.10
SRM_ALPHA = 0.001
TERMINAL = ("adopt", "reject")


@dataclass
class StepSummary:
    readouts: list[dict[str, Any]] = field(default_factory=list)
    paused: list[dict[str, Any]] = field(default_factory=list)
    concluded: list[dict[str, Any]] = field(default_factory=list)
    upcoming: list[dict[str, Any]] = field(default_factory=list)
    deviations: int = 0
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "readouts": self.readouts,
            "paused": self.paused,
            "concluded": self.concluded,
            "upcoming": self.upcoming,
            "deviations": self.deviations,
            "caveats": self.caveats,
        }


# ---------------------------------------------------------------- observations


@dataclass(frozen=True)
class SlotOutcome:
    slot: PlannedSlot
    arms: dict[str, int]  # factor -> original assignment (ITT)
    blocks: dict[str, str]
    measured: bool
    y: float | None  # I48 estimate from the score of the blind forecast
    f: float  # blind forecast median


def slot_outcomes(session: Session, exp: Experiment) -> list[SlotOutcome]:
    excluded = set(
        session.scalars(
            select(Deviation.slot_id).where(Deviation.experiment_id == exp.id, Deviation.kind == "excluded")
        )
    )
    by_slot: dict[int, list[Assignment]] = {}
    for a in engine.assignments_of(session, exp):
        by_slot.setdefault(a.slot_id, []).append(a)
    out = []
    for slot_id, rows in by_slot.items():
        if slot_id in excluded:
            continue
        slot = session.get(PlannedSlot, slot_id)
        assert slot is not None
        fc = session.get(Forecast, rows[0].blind_forecast_id)
        assert fc is not None
        y: float | None = None
        if slot.state == "posted" and slot.matched_post_id is not None:
            post = session.get(Post, slot.matched_post_id)
            score = session.scalar(select(ScoreRow).where(ScoreRow.forecast_id == fc.id))
            if post is not None and post.deleted_at is None and score is not None:
                y = float(score.outcome)
        out.append(
            SlotOutcome(
                slot=slot,
                arms={a.factor: a.arm for a in rows},
                blocks={a.factor: a.block_key for a in rows},
                measured=all(a.measured for a in rows),
                y=y,
                f=float(fc.quantiles["q50"]),
            )
        )
    return sorted(out, key=lambda o: o.slot.planned_at)


def per_post_observations(outcomes: list[SlotOutcome], tz: ZoneInfo) -> dict[str, list[stats.Observation]]:
    """{factor: observations}, ITT on the original arms, with the previous post's arms (carryover)."""
    observed = [o for o in outcomes if o.y is not None]
    by_factor: dict[str, list[stats.Observation]] = {}
    prev: dict[str, int] = {}
    for o in observed:
        assert o.y is not None
        for factor, block in o.blocks.items():
            by_factor.setdefault(factor, []).append(
                stats.Observation(
                    slot_key=o.slot.planned_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),
                    block=block,
                    day=o.slot.planned_at.astimezone(tz).date(),
                    log_y=math.log1p(o.y),
                    log_f=math.log1p(o.f),
                    arms=dict(o.arms),
                    prev_arms=dict(prev),
                )
            )
        prev = dict(o.arms)
    return by_factor


def switchback_observations(
    outcomes: list[SlotOutcome], metric: str, tz: ZoneInfo
) -> dict[str, list[stats.Observation]]:
    """One observation per 4-day block (its measured days), blocked by block pair, so the
    randomization test re-draws exactly the design that was run."""
    days: dict[tuple[str, Any], list[SlotOutcome]] = {}
    for o in outcomes:
        if o.y is None or not o.measured:
            continue
        factor, block = next(iter(o.blocks.items()))
        days.setdefault((block, o.slot.planned_at.astimezone(tz).date()), []).append(o)
    blocks: dict[str, list[tuple[float, float, Any, int, str]]] = {}
    for (block, day), group in days.items():
        ys = [g.y for g in group if g.y is not None]
        fs = [g.f for g in group]
        if metric == "daily_sum_i48":
            ly, lf = math.log1p(sum(ys)), math.log1p(sum(fs))
        else:
            ly = sum(math.log1p(y) for y in ys) / len(ys)
            lf = sum(math.log1p(f) for f in fs) / len(fs)
        factor = next(iter(group[0].arms))
        blocks.setdefault(block, []).append((ly, lf, day, group[0].arms[factor], factor))
    out: dict[str, list[stats.Observation]] = {}
    for block, vals in sorted(blocks.items()):
        pair = block.rsplit(":", 1)[0]
        factor = vals[0][4]
        out.setdefault(factor, []).append(
            stats.Observation(
                slot_key=block,
                block=pair,
                day=min(v[2] for v in vals),
                log_y=sum(v[0] for v in vals) / len(vals),
                log_f=sum(v[1] for v in vals) / len(vals),
                arms={factor: vals[0][3]},
            )
        )
    return out


def observations(session: Session, settings: Settings, exp: Experiment) -> dict[str, list[stats.Observation]]:
    tz = engine.account_tz(session, settings)
    outcomes = slot_outcomes(session, exp)
    if exp.design == "switchback":
        return switchback_observations(outcomes, exp.primary_metric, tz)
    return per_post_observations(outcomes, tz)


# ---------------------------------------------------------------- guardrails (MODEL §6.4)


def _pause_arm(
    session: Session, exp: Experiment, factor: str, bound: float, now: datetime, run_id: int | None
) -> dict[str, Any]:
    paused = dict(exp.paused_arms or {})
    paused[factor] = {"at": now.isoformat(), "bound": round(bound, 4), "reason": "arm_harm"}
    exp.paused_arms = paused
    reverted = engine.revert_to_control(
        session,
        exp,
        factor,
        "reverted_after_pause",
        f"arm harm: upper bound {math.exp(bound):.2f}x is below 0.8x",
        now,
        run_id,
    )
    if set(paused) >= {f["name"] for f in exp.factors}:
        exp.status = "paused"
        exp.pause_reason = "every arm paused by the arm-harm guardrail"
    exp.updated_at = now
    return {
        "experiment": exp.key,
        "factor": factor,
        "reason": "arm_harm",
        "bound_ratio": math.exp(bound),
        "reverted_slots": reverted,
    }


def _lost(session: Session, o: SlotOutcome) -> bool:
    return o.slot.matched_post_id is not None and _deleted(session, o.slot.matched_post_id)


def check_arm_harm(
    session: Session,
    exp: Experiment,
    obs: dict[str, list[stats.Observation]],
    now: datetime,
    run_id: int | None,
    outcomes: list[SlotOutcome] | None = None,
) -> list[dict[str, Any]]:
    """Pause an arm when the CS upper bound on treated-minus-control log(y/f) falls below log 0.8.

    Only randomization blocks whose every post has its outcome (or is lost for good: missed,
    deleted) enter the sequence, so a block's contrast never changes after it enters."""
    if exp.design != "per_post":
        return []
    outcomes = outcomes if outcomes is not None else slot_outcomes(session, exp)
    out = []
    for factor, rows in obs.items():
        if factor in (exp.paused_arms or {}):
            continue
        open_blocks = {
            o.blocks[factor]
            for o in outcomes
            if factor in o.blocks
            and (
                o.slot.state == "planned"
                or (o.slot.state == "posted" and o.y is None and not _lost(session, o))
            )
        }
        done = [o for o in rows if o.block not in open_blocks]
        treated = [o for o in done if o.arms.get(factor) == 1]
        control = [o for o in done if o.arms.get(factor) == 0]
        bound = stats_compat.harm_bound(
            [o.log_y - o.log_f for o in treated],
            [o.log_y - o.log_f for o in control],
            [o.block for o in treated],
            [o.block for o in control],
        )
        if bound < stats.ARM_HARM_THRESHOLD:
            out.append(_pause_arm(session, exp, factor, bound, now, run_id))
    return out


def unfollow_rates(
    session: Session, account_id: int, started_at: datetime, until: datetime
) -> tuple[list[float], list[float]]:
    """Unfollows a day over each run interval (new followers minus net change), before the
    experiment (last 4 weeks) and since it started."""
    snaps = session.scalars(
        select(FollowerSnapshot)
        .where(
            FollowerSnapshot.account_id == account_id,
            FollowerSnapshot.fetched_at <= until,
            FollowerSnapshot.fetched_at >= started_at - UNFOLLOW_BASELINE,
        )
        .order_by(FollowerSnapshot.fetched_at, FollowerSnapshot.id)
    ).all()
    before, during = [], []
    for prev, cur in itertools.pairwise(snaps):
        if cur.new_followers is None:
            continue
        days = (cur.fetched_at - prev.fetched_at).total_seconds() / 86400
        if days <= 0:
            continue
        rate = max(0, cur.new_followers - (cur.followers - prev.followers)) / days
        if cur.fetched_at <= started_at:
            before.append(rate)
        elif prev.fetched_at >= started_at:
            during.append(rate)
    return before, during


def unfollow_breach(baseline: list[float], during: list[float], alpha: float = 0.05) -> tuple[bool, float]:
    """Lower confidence-sequence bound on the mean log ratio of unfollows to the baseline mean;
    a breach when it clears log 1.25. Needs 3 baseline and 3 experiment intervals."""
    if len(baseline) < 3 or len(during) < 3:
        return False, -math.inf
    base = sum(baseline) / len(baseline)
    values = [math.log((1 + r) / (1 + base)) for r in during]
    lower = -stats.cs_upper_bound([-v for v in values], alpha)
    return lower > UNFOLLOW_THRESHOLD, lower


def check_unfollows(
    session: Session, exp: Experiment, cutoff: datetime, now: datetime, run_id: int | None
) -> tuple[dict[str, Any] | None, str | None]:
    if exp.status == "paused" or exp.started_at is None:
        return None, None
    baseline, during = unfollow_rates(session, exp.account_id, exp.started_at, cutoff)
    if len(baseline) < 3:
        return (
            None,
            f"{exp.key}: no unfollow baseline yet (follower lists not collected); guardrail inactive.",
        )
    breach, lower = unfollow_breach(baseline, during)
    if not breach:
        return None, None
    exp.status = "paused"
    exp.pause_reason = f"unfollows above the pre-experiment baseline (lower bound {math.exp(lower):.2f}x)"
    exp.updated_at = now
    reverted = engine.revert_to_control(
        session, exp, None, "reverted_after_pause", exp.pause_reason, now, run_id
    )
    return {
        "experiment": exp.key,
        "factor": None,
        "reason": "unfollows",
        "bound_ratio": math.exp(lower),
        "reverted_slots": reverted,
    }, None


def log_deviations(session: Session, exp: Experiment, now: datetime, run_id: int | None) -> int:
    n = 0
    for o in slot_outcomes(session, exp):
        if o.slot.state == "missed":
            n += engine.record_deviation(
                session, exp, o.slot.id, "", "missed_slot", "no post matched", now, run_id
            )
        elif o.slot.matched_post_id is not None:
            post = session.get(Post, o.slot.matched_post_id)
            if post is not None and post.deleted_at is not None:
                n += engine.record_deviation(
                    session, exp, o.slot.id, "", "deleted_post", f"post:{post.id} deleted", now, run_id
                )
    return n


# ---------------------------------------------------------------- readouts (MODEL §6.2, §6.3)


def health_checks(
    session: Session,
    settings: Settings,
    exp: Experiment,
    obs: dict[str, list[stats.Observation]],
    cutoff: datetime,
) -> dict[str, Any]:
    outcomes = slot_outcomes(session, exp)
    checks: dict[str, Any] = {}
    srm = {}
    for factor, rows in obs.items():
        n_t = sum(o.arms.get(factor, 0) for o in rows)
        n = len(rows)
        p = float(sps.binomtest(n_t, n, 0.5).pvalue) if n else 1.0
        srm[factor] = {"treated": n_t, "control": n - n_t, "p": round(p, 4), "flag": p < SRM_ALPHA}
    checks["srm"] = srm
    pending = [
        o.slot.id
        for o in outcomes
        if o.slot.state == "posted" and o.y is None and o.slot.planned_at < cutoff - FINAL_DELAY
    ]
    checks["maturity"] = {"complete": not pending, "awaiting_capture": pending}
    attrition = {}
    for factor in {f["name"] for f in exp.factors}:
        table = {0: [0, 0], 1: [0, 0]}  # arm -> [lost, kept]
        for o in outcomes:
            if factor not in o.arms or o.slot.planned_at > cutoff:
                continue
            lost = o.slot.state == "missed" or (
                o.slot.state == "posted"
                and o.y is None
                and o.slot.matched_post_id is not None
                and _deleted(session, o.slot.matched_post_id)
            )
            table[o.arms[factor]][0 if lost else 1] += 1
        p = float(sps.fisher_exact([table[1], table[0]]).pvalue) if sum(map(sum, table.values())) else 1.0
        attrition[factor] = {
            "treated_lost": table[1][0],
            "control_lost": table[0][0],
            "p": round(p, 4),
            "flag": p < 0.05,
        }
    checks["missed_or_deleted_by_arm"] = attrition
    bad = []
    for a in engine.assignments_of(session, exp):
        fc = session.get(Forecast, a.blind_forecast_id)
        if (
            fc is None
            or fc.kind != "treatment_blind"
            or not fc.frozen_at < a.drawn_at
            or fc.manifest_hash != a.blind_manifest_hash
        ):
            bad.append(a.id)
    checks["blind_forecast_predates_draw"] = {"ok": not bad, "violations": bad}
    checks["draws_reproduce_from_seed"] = {"ok": not engine.verify_draws(session, settings, exp)}
    regimes = []
    if exp.started_at is not None:
        regimes = [
            f"regime:{r.id} ({r.kind}, {r.started_at:%Y-%m-%d})"
            for r in session.scalars(
                select(Regime).where(
                    Regime.workspace_id == settings.workspace_id,
                    Regime.started_at >= exp.started_at,
                    Regime.started_at <= cutoff,
                )
            )
        ]
    checks["regime_change_in_window"] = {"flag": bool(regimes), "regimes": regimes}
    checks["ok"] = (
        not any(v["flag"] for v in srm.values())
        and not any(v["flag"] for v in attrition.values())
        and not bad
        and checks["draws_reproduce_from_seed"]["ok"]
        and not regimes
    )
    return checks


def _deleted(session: Session, post_id: int) -> bool:
    post = session.get(Post, post_id)
    return post is not None and post.deleted_at is not None


def analyse(
    exp: Experiment, obs: dict[str, list[stats.Observation]], at_horizon: bool
) -> dict[str, dict[str, Any]]:
    """Per-factor posterior, decision and randomization p (factors already decided keep theirs)."""
    names = [f["name"] for f in exp.factors]
    out: dict[str, dict[str, Any]] = {}
    for factor in names:
        rows = obs.get(factor, [])
        n_t = sum(o.arms.get(factor, 0) for o in rows)
        n_c = len(rows) - n_t
        if n_t < 2 or n_c < 2:
            out[factor] = {
                "decision": "continue",
                "reason": f"too early: {n_t} treated and {n_c} control posts have matured",
                "n_treated": n_t,
                "n_control": n_c,
                "randomization_p": None,
            }
            continue
        in_model = [k for k in names if all(k in o.arms for o in rows)]
        post = stats.analyze(rows, in_model, seed=exp.seed)[factor]
        dec = stats.decide(post, at_horizon=at_horizon, delta=exp.delta)
        p = stats.randomization_test(rows, factor, seed=exp.seed)
        lo, hi = post.interval(0.8)
        p_pos = post.p_greater(0.0)
        disagree = (p < 0.05) != (p_pos >= 0.975 or p_pos <= 0.025)
        prior = (exp.decisions or {}).get(factor)
        out[factor] = {
            "decision": prior["decision"] if prior else dec.outcome,
            "reason": prior["reason"] if prior else dec.reason,
            "decided_at_look": prior["look"] if prior else None,
            "effect_ratio": round(post.ratio, 4),
            "effect_log_mean": round(post.mean, 4),
            "effect_log_sd": round(post.sd, 4),
            "interval80_ratio": [round(math.exp(lo), 4), round(math.exp(hi), 4)],
            "p_gt_delta": round(dec.p_gt_delta, 4),
            "p_lt_zero": round(dec.p_lt_zero, 4),
            "expected_loss": round(dec.expected_loss, 5),
            "n_treated": post.n_treated,
            "n_control": post.n_control,
            "randomization_p": round(p, 5),
            "posterior_disagrees_with_randomization_test": disagree,
            "confirmed": False,
        }
    return out


def _overall(factors: dict[str, dict[str, Any]]) -> str:
    decisions: set[str] = {str(v["decision"]) for v in factors.values()}
    if len(decisions) == 1:
        return decisions.pop()
    if decisions <= set(TERMINAL):
        return "mixed"
    if "near_threshold" in decisions:
        return "near_threshold"
    return "continue"


def _write_readout(
    session: Session,
    exp: Experiment,
    run: Run,
    look: str,
    week: int,
    factors: dict[str, dict[str, Any]],
    checks: dict[str, Any],
    final: bool,
    now: datetime,
) -> Readout:
    row = Readout(
        workspace_id=exp.workspace_id,
        experiment_id=exp.id,
        run_id=run.id,
        look=look,
        experiment_week=week,
        protocol_version=exp.protocol_version,
        factors=factors,
        decision=_overall(factors),
        randomization_p={k: v.get("randomization_p") for k, v in factors.items()},
        health_checks=checks,
        final=final,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row


def elond_state(session: Session, settings: Settings) -> stats.ELond:
    """The stream replayed from ``elond_stream``; its gamma sequence is the one its first entry used."""
    rows = session.scalars(
        select(ELondEntry).where(ELondEntry.workspace_id == settings.workspace_id).order_by(ELondEntry.t)
    ).all()
    return stats_compat.elond(
        alpha=rows[0].alpha if rows else ELOND_ALPHA,
        tests=len(rows),
        rejections=sum(1 for r in rows if r.rejected),
        history=[
            {
                "t": r.t,
                "label": f"exp:{r.experiment_id}:{r.factor}",
                "e": r.e_value,
                "alpha_t": r.alpha_t,
                "rejected": r.rejected,
            }
            for r in rows
        ],
        gamma=rows[0].gamma_sequence if rows else None,
    )


def emit_e_values(
    session: Session,
    settings: Settings,
    exp: Experiment,
    factors: dict[str, dict[str, Any]],
    obs: dict[str, list[stats.Observation]],
    run: Run,
    now: datetime,
) -> list[dict[str, Any]]:
    """One e-value per concluded factor into e-LOND (FDR 0.10); idempotent per (experiment, factor).
    The e-value is the mSPRT likelihood ratio (MODEL §6.7); the randomization p stays in the
    readout as MODEL §6.2's check."""
    out = []
    for factor, summary in factors.items():
        p = summary.get("randomization_p")
        if p is None:
            continue
        existing = session.scalar(
            select(ELondEntry).where(ELondEntry.experiment_id == exp.id, ELondEntry.factor == factor)
        )
        if existing is None:
            state = elond_state(session, settings)
            e, kind = stats_compat.e_value(obs.get(factor, []), factor, float(p))
            rejected = state.test(e, f"{exp.key}:{factor}")
            h = state.history[-1]
            existing = ELondEntry(
                workspace_id=settings.workspace_id,
                experiment_id=exp.id,
                factor=factor,
                t=int(h["t"]),
                e_value=e,
                e_kind=kind,
                gamma_sequence=stats_compat.gamma_of(state),
                alpha=state.alpha,
                alpha_t=float(h["alpha_t"]),
                rejected=rejected,
                randomization_p=float(p),
                run_id=run.id,
                created_at=now,
            )
            session.add(existing)
            session.flush()
        summary["confirmed"] = bool(existing.rejected)
        summary["e_value"] = round(existing.e_value, 4)
        summary["e_kind"] = existing.e_kind
        summary["elond_alpha_t"] = existing.alpha_t
        out.append({"factor": factor, "e": existing.e_value, "rejected": existing.rejected, "t": existing.t})
    return out


def label(summary: dict[str, Any]) -> str:
    """ "confirmed" only after an e-LOND rejection (MODEL §6.7)."""
    d = summary["decision"]
    if summary.get("confirmed"):
        return f"{d} (confirmed)"
    return f"{d} (not confirmed)" if d in TERMINAL else d


def _conclude(
    session: Session,
    settings: Settings,
    exp: Experiment,
    run: Run,
    look: str,
    week: int,
    factors: dict[str, dict[str, Any]],
    obs: dict[str, list[stats.Observation]],
    checks: dict[str, Any],
    now: datetime,
) -> tuple[Readout, list[dict[str, Any]]]:
    elond = emit_e_values(session, settings, exp, factors, obs, run, now)
    readout = _write_readout(session, exp, run, look, week, factors, checks, True, now)
    exp.status = "concluded"
    exp.concluded_at = now
    exp.extension_offered_at = None
    exp.outcome = "; ".join(f"{k}: {label(v)}" for k, v in factors.items())
    exp.updated_at = now
    return readout, elond


def _record_decisions(exp: Experiment, factors: dict[str, dict[str, Any]], look: str) -> None:
    decisions = dict(exp.decisions or {})
    for k, v in factors.items():
        if v["decision"] in TERMINAL and k not in decisions:
            decisions[k] = {"decision": v["decision"], "reason": v["reason"], "look": look}
    exp.decisions = decisions


def _posted_awaiting_capture(session: Session, exp: Experiment) -> int:
    return sum(1 for o in slot_outcomes(session, exp) if o.slot.state == "posted" and o.y is None)


def _unposted_due(session: Session, exp: Experiment, cutoff: datetime) -> int:
    return sum(
        1 for o in slot_outcomes(session, exp) if o.slot.state == "planned" and o.slot.planned_at <= cutoff
    )


def read_out(
    session: Session, settings: Settings, exp: Experiment, run: Run, now: datetime, summary: StepSummary
) -> None:
    """Look only at week boundaries and at the final read (MODEL §6.3 batch-mode looks)."""
    if exp.started_at is None:
        return
    cutoff = run.cutoff_at
    end = engine.horizon_end(exp)
    assert end is not None
    looks = set(session.scalars(select(Readout.look).where(Readout.experiment_id == exp.id)))
    week = int((cutoff - exp.started_at) / timedelta(days=7))
    final_due = end + FINAL_DELAY
    if cutoff >= final_due:
        if "final" in looks:
            return
        waiting = _posted_awaiting_capture(session, exp) + _unposted_due(session, exp, cutoff)
        if waiting and cutoff < final_due + MATURITY_WAIT:
            summary.caveats.append(f"{exp.key}: final read waits for {waiting} post(s) to reach 48h.")
            return
        if (
            exp.extension_offered_at is not None
            and not exp.extension_used
            and cutoff < exp.extension_offered_at + EXTENSION_WINDOW
        ):
            return
        obs = observations(session, settings, exp)
        factors = analyse(exp, obs, at_horizon=True)
        checks = health_checks(session, settings, exp, obs, cutoff)
        horizon_week = math.ceil(exp.horizon_weeks)
        near = any(v["decision"] == "near_threshold" for v in factors.values())
        if near and not exp.extension_used and exp.extension_offered_at is None and "horizon" not in looks:
            _write_readout(session, exp, run, "horizon", horizon_week, factors, checks, False, now)
            exp.extension_offered_at = now
            summary.readouts.append(
                {
                    "experiment": exp.key,
                    "look": "horizon",
                    "decision": "near_threshold",
                    "note": "one extension of up to 50% is offered for 7 days (extend_experiment)",
                }
            )
            return
        _record_decisions(exp, factors, "final")
        readout, elond = _conclude(
            session, settings, exp, run, "final", horizon_week, factors, obs, checks, now
        )
        summary.readouts.append({"experiment": exp.key, "look": "final", "decision": readout.decision})
        summary.concluded.append(
            {"experiment": exp.key, "outcome": exp.outcome, "elond": elond, "health_ok": checks["ok"]}
        )
        return
    if cutoff >= end or week < 1:
        return
    look = f"week-{week}"
    if look in looks:
        return
    obs = observations(session, settings, exp)
    factors = analyse(exp, obs, at_horizon=False)
    checks = health_checks(session, settings, exp, obs, cutoff)
    _record_decisions(exp, factors, look)
    if all(v["decision"] in TERMINAL for v in factors.values()):
        readout, elond = _conclude(session, settings, exp, run, look, week, factors, obs, checks, now)
        summary.concluded.append(
            {"experiment": exp.key, "outcome": exp.outcome, "elond": elond, "health_ok": checks["ok"]}
        )
    else:
        readout = _write_readout(session, exp, run, look, week, factors, checks, False, now)
    summary.readouts.append({"experiment": exp.key, "look": look, "decision": readout.decision})


def next_look(exp: Experiment, cutoff: datetime) -> tuple[str, datetime] | None:
    end = engine.horizon_end(exp)
    if exp.started_at is None or end is None or exp.status in engine.TERMINAL_STATUSES:
        return None
    week = int((cutoff - exp.started_at) / timedelta(days=7))
    nxt = exp.started_at + timedelta(days=7 * (week + 1))
    if nxt < end:
        return f"week-{week + 1}", nxt
    return "final", end + FINAL_DELAY


# ---------------------------------------------------------------- the step


def run_step(session: Session, settings: Settings, run: Run, now: datetime) -> dict[str, Any]:
    summary = StepSummary()
    for exp in engine.list_all(session, settings, ("active", "paused")):
        summary.deviations += log_deviations(session, exp, now, run.id)
        obs = observations(session, settings, exp)
        summary.paused += check_arm_harm(session, exp, obs, now, run.id)
        paused, caveat = check_unfollows(session, exp, run.cutoff_at, now, run.id)
        if paused:
            summary.paused.append(paused)
        if caveat:
            summary.caveats.append(caveat)
        read_out(session, settings, exp, run, now, summary)
        nxt = next_look(exp, run.cutoff_at)
        if nxt is not None:
            summary.upcoming.append({"experiment": exp.key, "look": nxt[0], "at": nxt[1].isoformat()})
    session.flush()
    return summary.as_dict()
