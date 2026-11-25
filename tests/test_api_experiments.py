"""Contract tests for the experiment routes (M2): the Experiments screen and Account "Next posts".

The web app is the owner's surface: its writes act as the owner and its approve / reject go through
``services/approvals.py``. Settings use labeler "none", no regime check and no backup, the app gets
no regime fetcher and the fake provider, so nothing touches the network. Statistics depend on the
model and the seed, so they are checked as invariants, never as fixed numbers.
"""

import dataclasses
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from socialmgr.api.app import create_app
from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.db import migrate
from socialmgr.db.session import make_engine, session_factory
from socialmgr.experiments import instructions
from socialmgr.experiments.specs import AppliesTo
from socialmgr.experiments.suggestions import BY_KEY, SUGGESTIONS
from socialmgr.labelling.llm import NoLabeler
from socialmgr.services.fixtures import DEMO_SUGGESTIONS, load_experiment_demo
from socialmgr.services.plugins import load_plugins
from tests.conftest import FIXTURE_UNTIL, make_settings

ENVELOPE = {"summary", "confidence", "as_of", "model_version", "regime_id", "data_sufficiency", "caveats"}
HEADLINES = {"Adopt (not confirmed)", "Adopt · confirmed", "Reject", "Near threshold", "Keep going"}


def offline(tmp_path: Path) -> Settings:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def client(settings: Settings) -> TestClient:
    plugins = dataclasses.replace(load_plugins(settings), labeler=NoLabeler())
    return TestClient(create_app(settings, web_dist=None, plugins=plugins, regime_fetcher=None))


@pytest.fixture
def api(tmp_path: Path, fixture_db: Path) -> TestClient:
    settings = offline(tmp_path)
    shutil.copy(fixture_db, settings.db_path)
    return client(settings)


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The e2e experiments fixture: three weeks of E03 x E06 up to the session fixture's last run."""
    template = offline(tmp_path_factory.mktemp("demo"))
    engine = make_engine(template)
    migrate.upgrade(engine)
    with session_factory(engine)() as session:
        load_experiment_demo(session, template, load_plugins(template), until=FIXTURE_UNTIL)
        session.commit()
    engine.dispose()
    return template.db_path


@pytest.fixture
def demo(tmp_path: Path, demo_db: Path) -> TestClient:
    settings = offline(tmp_path)
    shutil.copy(demo_db, settings.db_path)
    return client(settings)


def ok(response: Any) -> Any:
    """The JSON body of a 200 response (an object, or a list for list routes)."""
    assert response.status_code == 200, response.text
    return response.json()


def local_tomorrow(api: TestClient, k: int = 1) -> date:
    tz = ZoneInfo(ok(api.get("/api/next-posts"))["tz"])
    return datetime.now(UTC).astimezone(tz).date() + timedelta(days=k)


def day(d: date, *times: str, **labels: str) -> dict[str, Any]:
    return {"day": d.isoformat(), "slots": [{"time": t, "labels": labels} for t in times]}


def propose(api: TestClient, **body: Any) -> dict[str, Any]:
    result: dict[str, Any] = ok(api.post("/api/experiments", json=body))
    return result


# ---------------------------------------------------------------- reads


def test_experiments_on_the_fixture_list_e01_to_e09_with_power(api: TestClient) -> None:
    body = ok(api.get("/api/experiments"))
    assert set(body) >= ENVELOPE | {"running", "waiting", "concluded", "suggestions", "approvals", "elond"}
    assert body["running"] == body["waiting"] == body["concluded"] == body["approvals"] == []
    assert [s["key"] for s in body["suggestions"]] == [f"E0{i}" for i in range(1, 10)]
    for s in body["suggestions"]:
        p = s["power"]
        numbers = [p["power_125"], p["power_15"], p["power_2"]]
        assert all(v is None or 0 <= v <= 1 for v in numbers) and p["text"].count("×") == 3
        assert s["already_running"] is False and s["factor"]["lever_label"]
        long = s["horizon_weeks"] > 8
        assert any("over 8 weeks" in r for r in s["needs_owner_because"]) == long
    assert body["elond"] == {"tests": 0, "rejections": 0, "alpha": 0.1}
    assert ok(api.get("/api/approvals")) == []
    assert api.get("/api/experiments/E99").status_code == 404


# ---------------------------------------------------------------- propose, slots, next posts


def test_owner_starts_a_suggestion_registers_slots_and_sees_next_posts(api: TestClient) -> None:
    started = propose(api, suggestion="E03")
    assert started["done"] is True and started["approval"] is None
    exp = started["experiment"]
    assert exp["key"] == "E03" and exp["status"] == "scheduled" and exp["created_by"] == "owner"
    assert exp["effect_text"] == "too early" and exp["week"] is None
    assert ok(api.get("/api/shell"))["counts"]["experiments_running"] == 1  # the sidebar count

    d1, d2 = local_tomorrow(api), local_tomorrow(api, 2)
    body = {"days": [day(d1, "07:00", "11:00", "16:00"), day(d2, "09:00", "15:00")]}
    reg = ok(api.post("/api/experiments/slots", json=body))
    assert reg["registered"] is True and reg["experiments"] == ["E03"]
    assert len(reg["slots"]) == 5
    arms = [a for s in reg["slots"] for a in s["arms"]]
    assert {a["arm"] for a in arms} == {"treatment", "control"}  # blocked within each day
    for slot in reg["slots"]:
        assert slot["forecast_kind"] == "treatment_blind" and slot["outcome_status"] == "planned"
        assert slot["forecast_q10"] <= slot["forecast_q50"] <= slot["forecast_q90"]
        (arm,) = slot["arms"]
        expected = instructions.arm_instruction("hook", arm["value"])
        assert arm["instruction"] == expected and expected in slot["instructions"]
        assert "lead with the result" in expected or "the way you usually do" in expected

    nxt = ok(api.get("/api/next-posts", params={"days": 3}))
    assert nxt["drawing"] == ["E03"] and d1.isoformat() in nxt["registered_days"]
    assert [s["slot"] for s in nxt["slots"]] == [s["slot"] for s in reg["slots"]]

    detail = ok(api.get("/api/experiments/E03"))
    assert set(detail) >= ENVELOPE | {"experiment", "readout", "slots", "past", "protocol_versions"}
    assert detail["experiment"]["status"] == "active" and detail["experiment"]["slots_assigned"] == 5
    assert detail["readout"] is None and detail["readouts"] == []
    assert detail["experiment"]["next_look"]["look"] == "week-1"
    assert detail["protocol_versions"][0]["version"] == 1 and detail["seed"] is not None
    (past,) = detail["past"]
    assert past["treated"]["above"] + past["controls"]["above"] == 0  # nothing posted yet

    again = ok(api.post("/api/experiments/slots", json={"days": [day(d1, "08:00")]}))
    assert again["registered"] is False and "already has experiment slots" in again["summary"]
    tooclose = ok(
        api.post("/api/experiments/slots", json={"days": [day(local_tomorrow(api, 3), "07:00", "08:00")]})
    )
    assert tooclose["registered"] is False and "3h" in tooclose["summary"]


def test_slots_for_one_experiment_only(api: TestClient) -> None:
    propose(api, suggestion="E03")
    propose(api, suggestion="E06")
    reg = ok(
        api.post("/api/experiments/E06/slots", json={"days": [day(local_tomorrow(api), "07:00", "12:00")]})
    )
    assert reg["registered"] is True and reg["experiments"] == ["E06"]
    assert {a["experiment"] for s in reg["slots"] for a in s["arms"]} == {"E06"}
    assert all(
        "image" in a["instruction"] or "text only" in a["instruction"]
        for s in reg["slots"]
        for a in s["arms"]
    )


def test_custom_experiment_and_validation(api: TestClient) -> None:
    custom = {"name": "CTA at the end", "hypothesis": "A CTA lifts follows.", "lever": "cta"}
    body = propose(api, custom={**custom, "control": "none", "treatment": "follow_cta"})
    assert body["done"] is True and body["experiment"]["factors"][0]["lever"] == "cta"
    assert body["experiment"]["key"].startswith("X")
    both = propose(api, suggestion="E03", custom={**custom, "control": "a", "treatment": "b"})
    assert both["done"] is False and "either" in both["summary"]
    unknown = propose(api, suggestion="E42")
    assert unknown["done"] is False and "E42" in unknown["summary"]
    bad = api.post(
        "/api/experiments", json={"custom": {**custom, "lever": "premium", "control": "a", "treatment": "b"}}
    )
    assert bad.status_code == 422  # account-level levers aren't offered


# ---------------------------------------------------------------- approvals


def test_long_horizon_waits_for_the_owner_who_approves(api: TestClient) -> None:
    body = propose(api, suggestion="E08")
    assert body["done"] is True and body["experiment"]["status"] == "pending_approval"
    card = body["approval"]
    assert card["title"] == "Start E08: Engager-peak window vs off-peak"
    assert "over 8 weeks" in card["detail"] and card["requested_by"] == "owner"
    (listed,) = ok(api.get("/api/approvals"))
    assert listed["id"] == card["id"] and listed["status"] == "pending"
    assert [a["id"] for a in ok(api.get("/api/experiments"))["approvals"]] == [card["id"]]
    assert ok(api.get("/api/shell"))["counts"]["approvals_pending"] == 1

    decided = ok(api.post(f"/api/approvals/{card['approval_id']}/approve", json={"note": "go"}))
    assert decided["done"] is True and decided["approval"]["status"] == "approved"
    assert decided["approval"]["decided_by"] == "owner" and decided["approval"]["note"] == "go"
    assert ok(api.get("/api/experiments/E08"))["experiment"]["status"] == "scheduled"
    assert ok(api.get("/api/approvals")) == []
    assert len(ok(api.get("/api/approvals", params={"status": "all"}))) == 1

    twice = ok(api.post(f"/api/approvals/{card['approval_id']}/approve"))
    assert twice["done"] is False and "already approved" in twice["summary"]
    assert api.post("/api/approvals/9999/approve").status_code == 404


def test_owner_rejects_a_long_custom_experiment(api: TestClient) -> None:
    custom = {"name": "Long hooks", "hypothesis": "h", "lever": "hook", "control": "a", "treatment": "b"}
    body = propose(api, custom={**custom, "horizon_weeks": 12})
    assert body["experiment"]["status"] == "pending_approval"
    decided = ok(api.post(f"/api/approvals/{body['approval']['approval_id']}/reject"))
    assert decided["done"] is True and decided["approval"]["status"] == "rejected"
    key = body["experiment"]["key"]
    assert ok(api.get(f"/api/experiments/{key}"))["experiment"]["status"] == "draft"


# ---------------------------------------------------------------- stop and extend


def test_extend_once_then_stop(api: TestClient) -> None:
    propose(api, suggestion="E06")
    ok(api.post("/api/experiments/slots", json={"days": [day(local_tomorrow(api), "07:00", "12:00")]}))
    ext = ok(api.post("/api/experiments/E06/extend"))
    assert ext["done"] is True and ext["experiment"]["horizon_weeks"] == 6.0
    assert ext["experiment"]["extension_used"] is True
    again = ok(api.post("/api/experiments/E06/extend"))
    assert again["done"] is False and "one extension" in again["summary"]

    stopped = ok(api.post("/api/experiments/E06/stop", json={"reason": "changed plans"}))
    assert stopped["done"] is True and stopped["experiment"]["status"] == "stopped"
    assert stopped["experiment"]["status_label"] == "Stopped"
    overview = ok(api.get("/api/experiments"))
    assert [e["key"] for e in overview["concluded"]] == ["E06"] and overview["running"] == []
    detail = ok(api.get("/api/experiments/E06"))
    assert {d["kind"] for d in detail["deviations"]} <= {"reverted_after_stop"}
    assert all(a["arm"] == "control" for s in detail["slots"] for a in s["arms"])  # treated slots reverted
    assert ok(api.post("/api/experiments/E06/stop", json={"reason": "x"}))["done"] is False
    assert api.post("/api/experiments/E06/stop", json={"reason": ""}).status_code == 422


# ---------------------------------------------------------------- readouts (demo fixture)


def test_readout_cards_and_past_against_blind_forecasts(demo: TestClient) -> None:
    overview = ok(demo.get("/api/experiments"))
    assert [e["key"] for e in overview["running"]] == list(DEMO_SUGGESTIONS)
    for e in overview["running"]:
        assert e["status"] == "active" and e["latest_look"] == "week-2" and e["week"] == 3
        assert 0 < e["posts_matured"] <= e["slots_assigned"]
        lo, hi = e["effect_interval80"]
        assert lo < e["effect_ratio"] < hi and "×" in e["effect_text"]
    running = {s["key"]: s["already_running"] for s in overview["suggestions"]}
    assert all(running[k] for k in DEMO_SUGGESTIONS)

    detail = ok(demo.get("/api/experiments/E03"))
    assert [r["look"] for r in detail["readouts"]] == ["week-1", "week-2"]
    readout = detail["readout"]
    assert readout["look"] == "week-2" and readout["final"] is False
    (f,) = readout["factors"]
    assert f["factor"] == "hook" and f["treatment"] == "outcome_first"
    lo, hi = f["interval80"]
    assert lo < f["effect_ratio"] < hi
    assert 0 <= f["p_gt_delta"] <= 1 and 0 <= f["p_lt_zero"] <= 1 and f["expected_loss"] >= 0
    assert 0 < f["randomization_p"] <= 1 and f["n_treated"] >= 2 and f["n_control"] >= 2
    assert f["headline"] in HEADLINES and f["confirmed"] is False
    assert "confirmed" not in f["label"] or "not confirmed" in f["label"]  # only e-LOND confirms
    assert "e-LOND" in f["elond"]
    checks = {h["name"]: h for h in readout["health"]}
    assert checks["Blind forecast before the draw"]["ok"] is True
    assert checks["Draws reproduce from the seed"]["ok"] is True

    # Always show how the past did: each arm's posts against their frozen treatment-blind forecasts.
    (past,) = detail["past"]
    scored = [s for s in detail["slots"] if s["outcome_status"] in ("above", "as_expected", "below")]
    counted = sum(past[arm][k] for arm in ("treated", "controls") for k in ("above", "as_expected", "below"))
    assert counted == len(scored) > 0
    for s in scored:
        assert s["forecast_kind"] == "treatment_blind" and s["outcome_i48"] is not None
        assert s["matched_post"].startswith("post:") and 0 <= s["pit"] <= 1


def test_suggestion_arms_all_have_plain_instructions() -> None:
    for sug in SUGGESTIONS:
        for f in sug.spec.factors:
            for value in (f.control, f.treatment):
                text = instructions.arm_instruction(f.lever, value)
                assert text and not text.startswith(f"{instructions.lever_label(f.lever).lower()}:"), (
                    sug.key,
                    value,
                )
    assert instructions.arm_instruction("cta", "follow_cta") == "call to action: follow cta"
    e02 = BY_KEY["E02"].spec.factors[0]
    assert instructions.applies_to_text(e02.applies_to) == "originals and quotes tagged link_post=true"
    assert instructions.applies_to_text(AppliesTo(labels_not={"content_kind": ["demo"]})).endswith(
        "not tagged content_kind=demo"
    )
    text = instructions.slot_instructions("2026-11-26 07:00", "UTC", [("link_placement", "first_self_reply")])
    assert text == (
        "Post at 2026-11-26 07:00 (UTC), within 30 minutes: "
        "put the link in the first self-reply, not in the post."
    )


def test_concurrent_experiments_enter_each_others_analysis_as_covariates(
    tmp_path: Path, demo_db: Path
) -> None:
    """MODEL §6.2 sums over every factor on a post: E03's analysis controls for E06's arm and vice versa."""
    from socialmgr.db.session import make_engine as _make_engine
    from socialmgr.experiments import engine as exp_engine
    from socialmgr.experiments import monitor

    settings = offline(tmp_path)
    shutil.copy(demo_db, settings.db_path)
    with session_factory(_make_engine(settings))() as session:
        e03 = exp_engine.get_experiment(session, settings, "E03")
        e06 = exp_engine.get_experiment(session, settings, "E06")
        obs03 = monitor.observations(session, settings, e03)
        obs06 = monitor.observations(session, settings, e06)
        f03, f06 = next(iter(obs03)), next(iter(obs06))
        assert all(f"E06:{f06}" in o.arms for o in obs03[f03])
        assert all(f"E03:{f03}" in o.arms for o in obs06[f06])
        # The covariate is the other experiment's original assignment on that slot.
        by_slot = {o.slot_key: o.arms[f06] for o in obs06[f06]}
        assert all(o.arms[f"E06:{f06}"] == by_slot[o.slot_key] for o in obs03[f03] if o.slot_key in by_slot)
