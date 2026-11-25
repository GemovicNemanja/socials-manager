"""M2 on a simulated clock: daily runs over a fake world whose outcomes react to each slot's arms.

Covered here: looks only at the first run after each experiment-week boundary and the final read
at horizon + 48h (MODEL §6.3); a full 4-week per-post experiment ending in a final readout and an
e-LOND entry; resume/idempotency of the experiments step; arm-harm pausing with reverted slots.
"""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Assignment, Briefing, Deviation, ELondEntry, PlannedSlot, Readout, Run
from socialmgr.experiments import engine, monitor, stats, stats_compat
from socialmgr.services import experiments as svc
from socialmgr.services import runs
from tests.experiment_sim import NOW, World, register_ahead, simulate

WS = tuple[Session, Settings, Any]


def _world(ws: WS, multiplier: float, key: str = "E03.hook") -> Any:
    session, settings, _ = ws
    world = World(session, effect=lambda arms: multiplier if arms.get(key) == 1 else 1.0)
    return world.plugins(settings)


def _force_interim_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interim looks return "continue" so a run reaches the guardrail or the final read. The
    decision rule itself belongs to the stats stream (tests/test_experiment_stats.py)."""
    real_decide = stats.decide

    def decide(post: stats.FactorPosterior, at_horizon: bool, delta: float = stats.DELTA) -> Any:
        d = real_decide(post, at_horizon, delta)
        if at_horizon:
            return d
        return stats.Decision("continue", "forced", d.p_gt_delta, d.p_lt_zero, d.expected_loss)

    monkeypatch.setattr(stats, "decide", decide)


def test_four_week_experiment_looks_at_week_boundaries_and_ends_in_e_lond(
    ws: WS, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E03 in a world where the treated hook triples impressions, run to the final read."""
    session, settings, _ = ws
    plugins = _world(ws, 3.0)
    _force_interim_continue(monkeypatch)
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    session.commit()
    log = simulate(session, settings, plugins, n_days=32, stop_when=lambda r: bool(r["concluded"]))
    exp = engine.get_experiment(session, settings, "E03")
    assert exp.status == "concluded", log.refusals
    assert exp.started_at is not None
    end = engine.horizon_end(exp)
    assert end is not None and end == exp.started_at + timedelta(weeks=4)

    # Looks: one per experiment week at the first run after its boundary; none at other runs.
    looked = {cut: [r["look"] for r in res["readouts"]] for cut, res in log.runs if res["readouts"]}
    cutoffs = [cut for cut, _ in log.runs]
    expected = {}
    for k in (1, 2, 3):
        boundary = exp.started_at + timedelta(weeks=k)
        expected[min(c for c in cutoffs if c >= boundary)] = [f"week-{k}"]
    final_due = end + monitor.FINAL_DELAY
    final_cut = min(c for c in cutoffs if c >= final_due)
    expected[final_cut] = ["final"]
    assert looked == expected

    readouts = session.scalars(
        select(Readout).where(Readout.experiment_id == exp.id).order_by(Readout.id)
    ).all()
    assert [r.final for r in readouts] == [False, False, False, True]
    final = readouts[-1]
    hook = final.factors["hook"]
    assert final.decision == hook["decision"] and hook["decision"] in ("adopt", "near_threshold", "reject")
    assert hook["n_treated"] + hook["n_control"] == 84  # 4 weeks x 3 posts a day, all matured
    assert hook["effect_ratio"] > 1.2
    checks = final.health_checks
    assert checks["maturity"]["complete"] and checks["blind_forecast_predates_draw"]["ok"]
    assert checks["draws_reproduce_from_seed"]["ok"] and not checks["srm"]["hook"]["flag"]
    assert not checks["regime_change_in_window"]["flag"]  # after the last seeded regime (Nov 18)

    entries = session.scalars(select(ELondEntry)).all()
    assert len(entries) == 1 and entries[0].t == 1 and entries[0].factor == "hook"
    entry = entries[0]
    # The e-value is the mSPRT likelihood ratio once stats has it (else the p calibrator, pre-merge).
    obs = monitor.observations(session, settings, exp)["hook"]
    e, kind = stats_compat.e_value(obs, "hook", hook["randomization_p"])
    assert (entry.e_value, entry.e_kind) == (pytest.approx(e), kind)
    fresh = stats_compat.elond(0.10, 0, 0, [], None)
    assert entry.rejected == fresh.test(entry.e_value)
    assert entry.alpha_t == pytest.approx(fresh.history[0]["alpha_t"])
    assert entry.gamma_sequence == stats_compat.gamma_of(fresh)
    assert hook["confirmed"] == entry.rejected  # "confirmed" only from an e-LOND rejection
    if kind == "msprt":  # a tripled hook over 84 posts clears e-LOND's first threshold
        assert entry.rejected
    label = svc.get_readout(session, settings, "E03").readout
    assert label is not None and label.labels["hook"].endswith("(confirmed)") == entries[0].rejected

    briefing = session.scalar(select(Briefing).order_by(Briefing.run_id.desc()).limit(1))
    assert briefing is not None and "Experiment E03 concluded" in briefing.summary

    # Re-running the step on the same run writes nothing new.
    last_run = session.scalar(select(Run).order_by(Run.id.desc()).limit(1))
    assert last_run is not None
    monitor.run_step(session, settings, last_run, last_run.cutoff_at)
    assert session.scalar(select(func.count(Readout.id))) == 4
    assert session.scalar(select(func.count(ELondEntry.id))) == 1


def test_mid_week_runs_dont_look_and_the_step_resumes_idempotently(ws: WS) -> None:
    session, settings, _ = ws
    plugins = _world(ws, 1.0)
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    session.commit()
    log = simulate(session, settings, plugins, n_days=7)  # runs Nov 26 .. Dec 2, all inside week 1
    assert all(not res["readouts"] for _, res in log.runs)
    assert session.scalar(select(func.count(Readout.id))) == 0

    # The first run after the week boundary (Dec 3) is interrupted after the experiments step.
    registry = runs.StepRegistry()
    for step in runs.STEPS.steps():
        registry.register(step.name, step.summary)(step.fn)
    calls = {"experiments": 0, "explode": 0}
    real = next(s.fn for s in runs.STEPS.steps() if s.name == "experiments")

    @registry.register("explode", "fails once", after="experiments")
    def _explode(ctx: runs.RunContext) -> dict[str, Any]:
        calls["explode"] += 1
        if calls["explode"] == 1:
            raise RuntimeError("disk full")
        return {}

    def counted(ctx: runs.RunContext) -> dict[str, Any]:
        calls["experiments"] += 1
        return real(ctx)

    registry._steps[[s.name for s in registry.steps()].index("experiments")] = runs.RunStep(
        "experiments", counted, "counted"
    )
    register_ahead(session, settings, plugins, log.runs[-1][0] + timedelta(hours=1), log)
    cut = log.runs[-1][0] + timedelta(days=1)
    with pytest.raises(runs.StepFailed, match="explode"):
        runs.execute(
            session,
            settings,
            plugins,
            clock=lambda: cut,
            cutoff=cut,
            registry=registry,
            skip=frozenset({"backup"}),
        )
    outcome = runs.execute(
        session, settings, plugins, clock=lambda: cut, registry=registry, skip=frozenset({"backup"})
    )
    assert outcome.resumed and calls == {"experiments": 1, "explode": 2}
    rows = session.scalars(select(Readout)).all()
    assert [r.look for r in rows] == ["week-1"] and not rows[0].final
    # Calling the step again for the same run adds nothing.
    run = session.get(Run, outcome.run_id)
    assert run is not None
    again = monitor.run_step(session, settings, run, cut)
    assert again["readouts"] == [] and session.scalar(select(func.count(Readout.id))) == 1


def test_arm_harm_pauses_the_arm_and_reverts_later_slots(ws: WS, monkeypatch: pytest.MonkeyPatch) -> None:
    """A world where the treated hook cuts impressions to 0.1x (log -2.3). The residual sd of
    log(y/f) is ~1.1 (the world's 0.8 plus prior-only forecast error), so arm_harm_bound's two
    one-sided sequences fall below log 0.8 at ~16 matured posts per arm: ~11 posting days plus the
    48h maturity lag, i.e. about two weeks after the start. Without forcing, the week-1 look already
    rejects this lever (P(effect < 0) >= 0.9), which is the decision rule working, not the guardrail.
    (A 0.5x harm needs ~200 posts per arm to trip the current bound; see the report.)"""
    session, settings, _ = ws
    plugins = _world(ws, 0.1)
    _force_interim_continue(monkeypatch)
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    session.commit()
    log = simulate(session, settings, plugins, n_days=21, stop_when=lambda r: bool(r["paused"]))
    paused_at, result = log.runs[-1]
    assert result["paused"], "the arm should pause within 3 weeks"
    exp = engine.get_experiment(session, settings, "E03")
    assert exp.started_at is not None and paused_at - exp.started_at <= timedelta(days=18)
    assert "hook" in exp.paused_arms and exp.status == "paused"  # its only arm: the experiment pauses
    assert exp.paused_arms["hook"]["bound"] < stats.ARM_HARM_THRESHOLD

    # Drawn-but-unposted treated slots reverted to control, each logged; assignments are untouched (ITT).
    reverted = session.scalars(select(Deviation).where(Deviation.kind == "reverted_after_pause")).all()
    assert reverted and result["paused"][0]["reverted_slots"] == len(reverted)
    for d in reverted:
        slot = session.get(PlannedSlot, d.slot_id)
        assert slot is not None and slot.planned_at > paused_at and slot.state == "planned"
        assert engine.effective_arms(session, slot.id) == {"E03.hook": 0}
        original = session.scalar(select(Assignment.arm).where(Assignment.slot_id == slot.id))
        assert original == 1
    # New draws stop: registering more days now finds no experiment to draw for.
    with pytest.raises(engine.ExperimentError, match="no scheduled or active"):
        register_ahead_strict(session, settings, plugins, paused_at, log)

    # The reverted slots are then posted as control: the world no longer applies the harm.
    simulate(session, settings, plugins, n_days=3, start=paused_at, register=False)
    posted = [session.get(PlannedSlot, d.slot_id) for d in reverted]
    assert any(s is not None and s.state == "posted" for s in posted)
    response = svc.get_experiment(session, settings, "E03")
    assert any("paused" in c.lower() for c in response.caveats)


def register_ahead_strict(session: Session, settings: Settings, plugins: Any, cut: Any, log: Any) -> None:
    today = (cut + timedelta(hours=1)).date()
    days = [today + timedelta(days=k) for k in range(1, 8) if today + timedelta(days=k) not in log.registered]
    engine.register_slots(
        session,
        settings,
        plugins,
        engine.day_plans(days or [today + timedelta(days=7)], [NOW.time()]),
        now=cut + timedelta(hours=1),
        actor="agent",
    )
