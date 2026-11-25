"""register_slots (MODEL §6.1): blind forecast first, then a seeded blocked draw over whole days."""

from datetime import time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Assignment, Experiment, Forecast, PlannedSlot
from socialmgr.experiments import engine
from socialmgr.experiments.specs import AppliesTo, DayPlan, FactorSpec, SlotSpec
from socialmgr.services import approvals, draws, pipeline
from tests.experiment_sim import NOW, SLOT_TIMES, TOMORROW, World, custom_spec, open_copy, sim_settings

WS = tuple[Session, Settings, Any]
FORMAT = FactorSpec(
    name="video",
    lever="format",
    control="Image",
    treatment="Narrated video",
    applies_to=AppliesTo(labels_not={"content_kind": ["thread"]}),
)
HOOK = FactorSpec(name="hook", lever="hook", control="standard", treatment="outcome_first")


def test_every_blind_forecast_predates_its_draw_and_hides_the_levers(ws: WS) -> None:
    session, settings, plugins = ws
    engine.propose_experiment(
        session, settings, plugins, actor="agent", now=NOW, spec=custom_spec("FMT", FORMAT)
    )
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    labels = {"format": "Image", "has_url": "true", "hook_type": "story"}
    plans = [
        DayPlan(day=TOMORROW + timedelta(days=i), slots=[SlotSpec(time=t, labels=labels) for t in SLOT_TIMES])
        for i in range(3)
    ]
    result = engine.register_slots(session, settings, plugins, plans, now=NOW, actor="agent")
    session.commit()
    assert len(result.slots) == 9 and result.experiments == ["E03", "FMT"]
    rows = session.scalars(select(Assignment)).all()
    assert len(rows) == 18  # factorial: every slot draws both experiments' factors
    lever_values = {"Image", "Narrated video", "standard", "outcome_first", "story"}
    for a in rows:
        fc = session.get(Forecast, a.blind_forecast_id)
        assert fc is not None and fc.kind == "treatment_blind" and fc.subject == f"slot:{a.slot_id}"
        assert a.drawn_at > fc.frozen_at  # the blind hash predates the draw
        assert a.blind_manifest_hash == fc.manifest_hash
        exp = session.get(Experiment, a.experiment_id)
        assert exp is not None and fc.model_version == exp.model_version  # the protocol's frozen model
        subject = fc.input_manifest["subject"]
        assert subject["format"] is None and subject["has_url"] is False
        assert not lever_values & {str(v) for v in subject.values()}
        blinded = set(fc.input_manifest["treatment_blind"]["blinded_fields"])
        assert {"format", "has_url", "hook", "hook_type"} <= blinded
        # ...and it recomputes bit-for-bit from its manifest with the frozen model.
        _v, _c, predicted = pipeline.forecast_from_manifest(
            engine.frozen_plugins(settings, plugins, exp), fc.input_manifest
        )
        assert draws.encode(predicted.samples)[0] == fc.draws_hash
    slot = session.get(PlannedSlot, rows[0].slot_id)
    assert slot is not None and slot.lever_context is not None
    assert set(slot.lever_context["experiments"]) == {"E03", "FMT"}


def test_blocks_are_balanced_within_each_day_and_reproduce_from_the_seed(
    ws: WS, tmp_path: Path, fixture_db: Path
) -> None:
    session, settings, plugins = ws
    spec = custom_spec("TWO", HOOK, FORMAT, seed=1234)
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, spec=spec)
    plans = engine.day_plans([TOMORROW + timedelta(days=i) for i in range(7)], SLOT_TIMES)
    engine.register_slots(session, settings, plugins, plans, now=NOW, actor="agent")
    session.commit()
    exp = engine.get_experiment(session, settings, "TWO")
    rows = engine.assignments_of(session, exp)
    by_block: dict[tuple[str, str], list[int]] = {}
    for a in rows:
        assert a.propensity == 0.5 and a.block_key.startswith("d:")
        by_block.setdefault((a.factor, a.block_key), []).append(a.arm)
    assert len(by_block) == 14
    assert all(len(arms) == 3 and sum(arms) in (1, 2) for arms in by_block.values())  # floor/ceil of k/2
    assert engine.verify_draws(session, settings, exp) == []

    # A second workspace with the same seed and schedule draws the same arms.
    other_settings = sim_settings(tmp_path / "b")
    other = open_copy(other_settings, fixture_db)
    other_plugins = World(other).plugins(other_settings)
    engine.propose_experiment(other, other_settings, other_plugins, actor="owner", now=NOW, spec=spec)
    engine.register_slots(other, other_settings, other_plugins, plans, now=NOW, actor="owner")
    again = engine.assignments_of(other, engine.get_experiment(other, other_settings, "TWO"))
    assert {(a.slot_key, a.factor, a.arm) for a in again} == {(a.slot_key, a.factor, a.arm) for a in rows}
    other.close()


def test_one_post_a_day_is_blocked_by_two_day_pairs(ws: WS) -> None:
    session, settings, plugins = ws
    engine.propose_experiment(
        session, settings, plugins, actor="agent", now=NOW, spec=custom_spec("ONE", HOOK)
    )
    days = [TOMORROW + timedelta(days=i) for i in range(5)]  # pairs (26, 27), (28, 29); the 30th alone
    engine.register_slots(
        session, settings, plugins, engine.day_plans(days, [time(9)]), now=NOW, actor="agent"
    )
    blocks: dict[str, list[int]] = {}
    for a in engine.assignments_of(session, engine.get_experiment(session, settings, "ONE")):
        blocks.setdefault(a.block_key, []).append(a.arm)
    assert blocks.keys() == {"p:2026-11-26/2026-11-27", "p:2026-11-28/2026-11-29", "d:2026-11-30"}
    assert sorted(blocks["p:2026-11-26/2026-11-27"]) == [0, 1]
    assert sorted(blocks["p:2026-11-28/2026-11-29"]) == [0, 1]


def test_register_slots_refuses_partial_far_repeated_and_crowded_days(ws: WS) -> None:
    session, settings, plugins = ws

    def register(days: list[Any], times: list[time]) -> None:
        engine.register_slots(
            session, settings, plugins, engine.day_plans(days, times), now=NOW, actor="agent"
        )

    with pytest.raises(engine.ExperimentError, match="no scheduled or active"):
        register([TOMORROW], SLOT_TIMES)
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    with pytest.raises(engine.ExperimentError, match="has started"):
        register([NOW.date()], SLOT_TIMES)
    with pytest.raises(engine.ExperimentError, match="more than 7 days"):
        register([NOW.date() + timedelta(days=8)], SLOT_TIMES)
    with pytest.raises(engine.ExperimentError, match="less than 3h"):
        register([TOMORROW], [time(7), time(9)])
    register([TOMORROW], SLOT_TIMES)
    with pytest.raises(engine.ExperimentError, match="already has experiment slots"):
        register([TOMORROW], [time(20)])


def test_a_switchback_day_keeps_only_its_condition_slots(ws: WS) -> None:
    session, settings, plugins = ws
    r = engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E09")
    assert r.approval is not None
    approvals.decide(session, settings, r.approval.id, "approve", now=NOW)
    plans = [
        DayPlan(
            day=TOMORROW + timedelta(days=i),
            slots=[SlotSpec(time=time(9)), SlotSpec(time=time(17))],
            treatment_slots=[SlotSpec(time=t) for t in (time(6), time(10), time(14), time(18))],
        )
        for i in range(7)
    ]
    result = engine.register_slots(session, settings, plugins, plans, now=NOW, actor="agent")
    exp = engine.get_experiment(session, settings, "E09")
    tz = engine.account_tz(session, settings)
    for p in plans:
        arm, _block, measured = engine.switchback_condition(exp, p.day, tz)
        kept = [rs for rs in result.slots if rs.slot.planned_at.date() == p.day and not rs.cancelled]
        assert len(kept) == (4 if arm else 2)  # E09: 2 vs 4 originals a day
        assert all(a.arm == arm and a.measured == measured for rs in kept for a in rs.arms)
        assert all(rs.blind_forecast.frozen_at < NOW + timedelta(seconds=1) for rs in kept)
    # Blocks 0 and 1 (days 1-4, 5-8) form a pair: one treated, one control.
    assert {engine.switchback_condition(exp, TOMORROW + timedelta(days=4 * b), tz)[0] for b in (0, 1)} == {
        0,
        1,
    }
    assert engine.verify_draws(session, settings, exp) == []
