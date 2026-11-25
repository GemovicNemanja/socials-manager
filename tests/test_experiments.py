"""Experiment engine (M2): schema, MODEL §6.9 permissions, approvals, slot registration, looks.

Real SQLite (a copy of the session fixture workspace), the fake provider, a simulated clock.
"""

import math
import warnings
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Approval, Base, PlannedSlot, ProtocolVersion
from socialmgr.experiments import engine
from socialmgr.experiments.specs import FactorSpec
from socialmgr.experiments.suggestions import SUGGESTIONS
from socialmgr.services import approvals
from socialmgr.services import experiments as svc
from socialmgr.services.plugins import ActivePlugins
from tests.experiment_sim import NOW, SLOT_TIMES, TOMORROW
from tests.experiment_sim import custom_spec as _custom

# ---------------------------------------------------------------- schema


def test_migration_matches_models(ws: tuple[Session, Settings, ActivePlugins]) -> None:
    session, _s, _p = ws
    ctx = MigrationContext.configure(session.connection())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the posts <-> planned_slots FK cycle predates M2
        diff = [d for d in compare_metadata(ctx, Base.metadata) if d[0] != "remove_table"]
    assert diff == []


@pytest.mark.parametrize("table", ["protocol_versions", "assignments", "elond_stream"])
def test_experiment_history_is_append_only(ws: tuple[Session, Settings, ActivePlugins], table: str) -> None:
    session, settings, plugins = ws
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    engine.register_slots(
        session, settings, plugins, engine.day_plans([TOMORROW], SLOT_TIMES), now=NOW, actor="agent"
    )
    if table == "elond_stream":
        session.execute(
            text(
                "INSERT INTO elond_stream (experiment_id, factor, t, e_value, e_kind, gamma_sequence, alpha, "
                "alpha_t, rejected, randomization_p, created_at, workspace_id) VALUES (1, 'hook', 1, 1.0, "
                "'msprt', '1/(t(t+1))', 0.1, 0.05, 0, 0.5, '2026-11-25 07:00:00', 1)"
            )
        )
    session.commit()
    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(text(f"UPDATE {table} SET workspace_id = 1"))
    session.rollback()
    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(text(f"DELETE FROM {table}"))


# ---------------------------------------------------------------- suggestions and permissions


def test_suggestions_are_e01_to_e09_with_live_power(ws: tuple[Session, Settings, ActivePlugins]) -> None:
    session, settings, _p = ws
    assert [s.key for s in SUGGESTIONS] == [f"E0{i}" for i in range(1, 10)]
    views = engine.suggestions(session, settings, NOW)
    rate = engine.posting_rate(session, settings, NOW)
    assert rate >= 1.0
    e03 = next(v for v in views if v.suggestion.key == "E03")
    assert e03.power.n_posts == round(rate * 28)
    assert set(e03.power.power) == {"1.25x", "1.5x", "2x"} and e03.power.mde_ratio_80 is not None
    e01 = next(v for v in views if v.suggestion.key == "E01")
    assert e01.power.posts_per_day == 1.0  # demo posts: no demo labels yet, so the 1/day floor
    e08 = next(v for v in views if v.suggestion.key == "E08")
    assert e08.power.power["1.5x"] == 0.29  # MODEL §6.8, 12 weeks


def test_auto_start_vs_approval(ws: tuple[Session, Settings, ActivePlugins]) -> None:
    session, settings, plugins = ws
    started = [
        engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key=k)
        for k in ("E03", "E06")
    ]
    assert all(r.experiment.status == "scheduled" and r.approval is None for r in started)
    six = engine.propose_experiment(
        session,
        settings,
        plugins,
        actor="owner",
        now=NOW,
        spec=_custom("SIX", FactorSpec(name="cta", lever="cta", control="none", treatment="follow"), weeks=6),
    )
    assert six.experiment.status == "scheduled"  # the owner's own 6-week experiment starts directly
    # A 4th concurrent experiment, and a 4th per-post factor, wait for the owner.
    fourth = engine.propose_experiment(
        session, settings, plugins, actor="agent", now=NOW, suggestion_key="E07"
    )
    assert fourth.experiment.status == "pending_approval" and fourth.approval is not None
    assert any("more than 3" in r for r in fourth.reasons)
    assert any("4 per-post factors" in r for r in fourth.reasons)
    # Horizons over 8 weeks and big cadence changes wait for the owner.
    e09 = engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E09")
    assert e09.experiment.status == "pending_approval"
    assert any("over 8 weeks" in r for r in e09.reasons) and any("cadence" in r for r in e09.reasons)
    # At most one switchback, counting pending ones.
    with pytest.raises(engine.ExperimentError, match="one switchback"):
        engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E08")
    # Account-level levers are refused outright; levers outside the list need the owner.
    with pytest.raises(ValueError, match="account-level"):
        _custom("BIO", FactorSpec(name="bio", lever="bio", control="a", treatment="b"))
    odd = engine.propose_experiment(
        session,
        settings,
        plugins,
        actor="agent",
        now=NOW,
        start=False,
        spec=_custom("ODD", FactorSpec(name="emoji", lever="emoji_count", control="0", treatment="3")),
    )
    assert any(
        "outside the approved" in r for r in engine.approval_reasons(session, settings, odd.experiment)
    )


def test_an_agent_cannot_approve_its_own_amendment(ws: tuple[Session, Settings, ActivePlugins]) -> None:
    session, settings, plugins = ws
    svc.propose_experiment(session, settings, plugins, suggestion_key="E03", now=NOW)
    amended = svc.amend_experiment(
        session,
        settings,
        plugins,
        "E03",
        engine.AmendRequest(delta=math.log(1.05), name="Hook v2"),
        reason="smaller δ",
        now=NOW,
    )
    assert amended.applied == ["name"]  # free field applied at once
    assert len(amended.approvals) == 1 and amended.approvals[0].kind == "amend_protocol"
    approval_id = amended.approvals[0].approval_id
    row = session.get(Approval, approval_id)
    assert row is not None and row.requested_by == "agent" and row.status == "pending"
    exp = engine.get_experiment(session, settings, "E03")
    assert exp.delta == pytest.approx(math.log(1.10))  # the frozen field is unchanged

    # The agent path has no way to decide, and the engine refuses any actor but the owner.
    assert not any("decide" in name or "approve" in name for name in dir(svc))
    for path in ("socialmgr/services/experiments.py", "socialmgr/agent_server.py"):
        source = (Path(__file__).parents[1] / path).read_text()
        assert "decide_approval" not in source and "import approvals" not in source
    with pytest.raises(engine.ApprovalForbidden):
        engine.decide_approval(session, settings, approval_id, "approve", actor="agent", now=NOW)
    assert session.get(Approval, approval_id).status == "pending"  # type: ignore[union-attr]

    out = approvals.decide(session, settings, approval_id, "approve", note="ok", plugins=plugins, now=NOW)
    assert out.status == "approved" and out.decided_by == "owner"
    exp = engine.get_experiment(session, settings, "E03")
    assert exp.delta == pytest.approx(math.log(1.05))
    versions = session.scalars(
        select(ProtocolVersion)
        .where(ProtocolVersion.experiment_id == exp.id)
        .order_by(ProtocolVersion.version)
    ).all()
    assert [v.created_by for v in versions] == ["agent", "agent", "owner"]
    with pytest.raises(engine.ExperimentError, match="already approved"):
        approvals.decide(session, settings, approval_id, "reject", now=NOW)


def test_owner_approves_a_long_experiment_and_rejects_an_exclusion(
    ws: tuple[Session, Settings, ActivePlugins],
) -> None:
    session, settings, plugins = ws
    r = svc.propose_experiment(session, settings, plugins, suggestion_key="E08", now=NOW)
    assert r.created and not r.started and r.approval is not None
    approvals.decide(session, settings, r.approval.approval_id, "approve", now=NOW)
    assert engine.get_experiment(session, settings, "E08").status == "scheduled"
    ex = svc.amend_experiment(
        session, settings, plugins, "E08", engine.AmendRequest(exclude_slots=[1]), reason="flop", now=NOW
    )
    assert ex.approvals[0].kind == "exclude_posts"
    approvals.decide(session, settings, ex.approvals[0].approval_id, "reject", now=NOW)
    assert svc.list_approvals(session, settings).approvals == []


def test_extension_once_and_stop(ws: tuple[Session, Settings, ActivePlugins]) -> None:
    session, settings, plugins = ws
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    engine.register_slots(
        session, settings, plugins, engine.day_plans([TOMORROW], SLOT_TIMES), now=NOW, actor="agent"
    )
    ext = svc.extend_experiment(session, settings, "E03", now=NOW)
    assert ext.applied == ["horizon_weeks"] and ext.experiment is not None
    assert ext.experiment.horizon_weeks == 6.0
    again = svc.extend_experiment(session, settings, "E03", now=NOW)
    assert again.experiment is None and "already used" in again.summary
    stopped = svc.stop_experiment(session, settings, "E03", reason="test", now=NOW)
    assert stopped.done and stopped.experiment is not None and stopped.experiment.status == "stopped"
    # Treated, unposted slots revert to control.
    for slot in session.scalars(select(PlannedSlot).where(PlannedSlot.experiment_id.is_not(None))):
        assert all(v == 0 for v in engine.effective_arms(session, slot.id).values())


def test_an_agent_can_auto_start_only_up_to_four_weeks(ws: tuple[Session, Settings, ActivePlugins]) -> None:
    """MODEL §6.9: agents may auto-start horizons up to 4 weeks; longer ones wait for the owner."""
    session, settings, plugins = ws
    lever = FactorSpec(name="cta", lever="cta", control="none", treatment="follow")
    four = engine.propose_experiment(
        session, settings, plugins, actor="agent", now=NOW, spec=_custom("FOUR", lever, weeks=4)
    )
    assert four.experiment.status == "scheduled"
    engine.stop_experiment(session, settings, "FOUR", reason="test", actor="agent", now=NOW)
    six = engine.propose_experiment(
        session, settings, plugins, actor="agent", now=NOW, spec=_custom("SIXA", lever, weeks=6)
    )
    assert six.experiment.status == "pending_approval" and six.approval is not None
    assert any("agents may start up to 4" in r for r in six.reasons)
