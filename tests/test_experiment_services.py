"""Experiment services and CLI: the unfollow guardrail, the stats adapter, the agent envelope on
every experiment service, and the manual CLI flow (propose, register slots, approve)."""

import math
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from socialmgr.cli import app
from socialmgr.core.config import Settings
from socialmgr.db.models import Deviation, FollowerSnapshot, Run
from socialmgr.experiments import engine, monitor, stats, stats_compat
from socialmgr.experiments.specs import DayPlan, SlotSpec
from socialmgr.services import experiments as svc
from tests.experiment_sim import NOW, SLOT_TIMES, TOMORROW

WS = tuple[Session, Settings, Any]
ENVELOPE = {"summary", "confidence", "as_of", "model_version", "regime_id", "data_sufficiency", "caveats"}


def test_unfollow_breach_needs_a_baseline_and_a_clear_rise() -> None:
    assert monitor.unfollow_breach([2, 2], [9, 9, 9, 9]) == (False, -math.inf)  # no baseline yet
    calm, _ = monitor.unfollow_breach([2, 3, 2, 3, 2], [2, 3, 3, 2, 2, 3])
    assert not calm
    spike, lower = monitor.unfollow_breach([2, 3, 2, 3, 2], [30, 28, 35, 31, 29, 33, 30, 32])
    assert spike and lower > monitor.UNFOLLOW_THRESHOLD


def test_an_unfollow_spike_pauses_the_whole_experiment(ws: WS) -> None:
    session, settings, plugins = ws
    engine.propose_experiment(session, settings, plugins, actor="agent", now=NOW, suggestion_key="E03")
    engine.register_slots(
        session,
        settings,
        plugins,
        engine.day_plans([TOMORROW, TOMORROW + timedelta(days=1)], SLOT_TIMES),
        now=NOW,
        actor="agent",
    )
    exp = engine.get_experiment(session, settings, "E03")
    assert exp.started_at is not None
    owner = exp.account_id
    # Synthetic follower snapshots: 3 unfollows a day before the start, 40 a day after.
    # The fixture's snapshots carry no follower lists (new_followers is None): replace them.
    session.execute(
        delete(FollowerSnapshot).where(FollowerSnapshot.fetched_at >= exp.started_at - timedelta(days=30))
    )
    followers = 1000
    runs = []
    for d in range(-10, 8):
        t = exp.started_at + timedelta(days=d, hours=6)
        run = Run(workspace_id=1, trigger="test", started_at=t, cutoff_at=t, status="finished", steps={})
        session.add(run)
        session.flush()
        new, lost = 10, (3 if d <= 0 else 40)
        followers += new - lost
        session.add(
            FollowerSnapshot(
                workspace_id=1,
                account_id=owner,
                run_id=run.id,
                fetched_at=t,
                followers=followers,
                new_followers=new,
            )
        )
        runs.append(t)
    session.flush()
    before, during = monitor.unfollow_rates(session, owner, exp.started_at, runs[-1])
    assert len(before) >= 3 and during and all(r == pytest.approx(40) for r in during)
    # Pausing at day 1 after the start: slots planned later revert to control.
    now = exp.started_at + timedelta(hours=1)
    paused, caveat = monitor.check_unfollows(session, exp, runs[-1], now, None)
    assert caveat is None and paused is not None and paused["reason"] == "unfollows"
    assert exp.status == "paused" and "unfollows" in (exp.pause_reason or "")
    treated = session.scalars(select(Deviation).where(Deviation.kind == "reverted_after_pause")).all()
    assert len(treated) == paused["reverted_slots"] > 0


def test_ols_effect_and_e_value_adapter() -> None:
    rng = np.random.default_rng(0)
    obs = []
    for i in range(80):
        a = i % 2
        lf = float(rng.normal(5, 0.5))
        log_y = lf + 0.7 * a + float(rng.normal(0, 0.5))
        obs.append(stats.Observation(f"s{i}", f"d{i // 2}", date(2026, 12, 1), log_y, lf, {"x": a}))
    effect = stats_compat.ols_effect(obs, "x")
    assert effect is not None and abs(effect[0] - 0.7) < 0.3 and 0 < effect[1] < 0.2
    e, kind = stats_compat.e_value(obs, "x", randomization_p=0.01)
    assert kind in ("msprt", "p_calibrator") and e > 1
    assert stats_compat.ols_effect(obs[:2], "x") is None  # no residual degrees of freedom


def test_every_experiment_service_returns_the_agent_envelope(ws: WS) -> None:
    session, settings, plugins = ws
    plan = [DayPlan(day=TOMORROW, slots=[SlotSpec(time=t) for t in SLOT_TIMES])]
    responses = [
        svc.power_check(session, settings, suggestion_key="E03", now=NOW),
        svc.propose_experiment(session, settings, plugins, suggestion_key="E03", now=NOW),
        svc.register_slots(session, settings, plugins, plan, now=NOW),
        svc.get_experiments(session, settings, now=NOW),
        svc.get_experiment(session, settings, "E03", now=NOW),
        svc.get_readout(session, settings, "E03"),
        svc.list_approvals(session, settings),
        svc.amend_experiment(session, settings, plugins, "E03", engine.AmendRequest(name="n"), "r", now=NOW),
        svc.extend_experiment(session, settings, "E03", now=NOW),
        svc.propose_experiment(session, settings, plugins, suggestion_key="E06", start=False, now=NOW),
        svc.schedule_experiment(session, settings, "E06", now=NOW),
        svc.stop_experiment(session, settings, "E06", reason="r", now=NOW),
        svc.get_experiment(session, settings, "nope", now=NOW),
    ]
    for r in responses:
        data = r.model_dump(mode="json")
        assert set(data) >= ENVELOPE and data["confidence"] in ("solid", "suggestive", "too early")
    reg = responses[2]
    assert isinstance(reg, svc.RegisterSlotsResponse) and reg.registered and len(reg.slots) == 3
    assert all(s.blind_forecast.startswith("forecast:") and s.arms for s in reg.slots)
    assert "Post at" in reg.slots[0].instructions
    assert responses[-1].summary.startswith("Not done")


def test_cli_manual_flow(tmp_path: Path, fixture_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(fixture_db, data / "socialmgr.sqlite3")
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(data))
    monkeypatch.setenv("SOCIALMGR_PLUGINS__LABELER", "none")
    runner = CliRunner()

    def ok(*args: str) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    assert "E09" in ok("experiments", "suggest")
    assert "E03 is scheduled" in ok("experiments", "propose", "E03")
    slots = ok("experiments", "slots", "--times", "07:00,11:00,16:00", "--days", "2")
    assert slots.count("slot:") == 6 and "hook = " in slots
    listing = ok("experiments")
    assert "E03" in listing and "active" in listing
    assert "waits" not in ok("experiments", "show", "E03")
    proposed = ok("experiments", "propose", "E08")
    assert "Needs approval 1" in proposed
    assert "start_experiment" in ok("approvals")
    assert "approved" in ok("approve", "1", "--note", "go")
    assert "No pending approvals" in ok("approvals")
    refused = runner.invoke(app, ["reject", "1"])
    assert refused.exit_code == 1 and "already approved" in refused.output
