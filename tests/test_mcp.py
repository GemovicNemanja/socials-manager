"""Contract tests for every MCP tool, resource and prompt (IMPLEMENTATION_PLAN.md §8)."""

import asyncio
import dataclasses
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from socialmgr.agent_server import create_server
from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.db.models import Forecast, PlannedSlot
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings

TOOLS = {
    "get_briefing",
    "start_run",
    "get_run_status",
    "get_posts",
    "get_post",
    "get_forecast",
    "register_planned_post",
    "get_schedule",
    "set_label",
    "get_account_overview",
    "export_data",
    "get_experiments",
    "get_experiment",
    "power_check",
    "propose_experiment",
    "register_slots",
    "schedule_experiment",
    "amend_experiment",
    "stop_experiment",
    "get_readout",
    "list_approvals",
}
ENVELOPE = {"summary", "confidence", "as_of", "model_version", "regime_id", "data_sufficiency", "caveats"}


@pytest.fixture
def mcp_settings(tmp_path: Path, fixture_db: Path) -> Settings:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_db, settings.db_path)
    return settings


@pytest.fixture
def server(mcp_settings: Settings) -> Any:
    plugins = dataclasses.replace(load_plugins(mcp_settings), labeler=NoLabeler())
    return create_server(mcp_settings, plugins, regime_fetcher=None)


def call(server: Any, name: str, **args: Any) -> dict[str, Any]:
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result
    data: dict[str, Any] = result.structured_content
    assert set(data) >= ENVELOPE, f"{name} is missing envelope fields"
    assert data["confidence"] in ("solid", "suggestive", "too early")
    return data


def test_tool_list_is_exactly_the_m1_set_and_none_can_act_on_x(server: Any) -> None:
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == TOOLS
    assert not any(
        word in n for n in names for word in ("post_to", "tweet", "like", "reply", "follow", "approve")
    )
    for t in tools:
        assert t.description and t.output_schema, t.name
        if t.name.startswith("get_"):
            assert t.annotations.read_only_hint is True, t.name


def test_get_briefing_and_run_status(server: Any) -> None:
    b = call(server, "get_briefing")
    assert b["run_id"] == 43 and "reached 48h" in b["summary"]
    assert "Account: Impressions, last 7 days" in b["summary"] and b["data"]["account"]
    # 123 matured posts, but the M1 champion is prior-only: confidence must not claim more.
    assert b["data_sufficiency"]["owner_n"] > 60 and b["confidence"] == "too early"
    assert b["data_sufficiency"]["reason"] == "prior_only_uncalibrated"
    status = call(server, "get_run_status")
    assert status["unfinished"] is None and status["latest"]["status"] == "finished"
    assert [s["status"] for s in status["latest"]["steps"]][:-1] == ["done"] * 9  # backup skipped in fixtures


def test_start_run_collects_and_returns_a_new_briefing(server: Any, mcp_settings: Settings) -> None:
    b = call(server, "start_run")
    assert b["run_id"] == 44
    engine = make_engine(mcp_settings)
    with session_factory(engine)() as s:
        assert s.scalar(select(func.count(PlannedSlot.id))) == 0


def test_get_posts_and_get_post(server: Any) -> None:
    posts = call(server, "get_posts", status="all", limit=5)
    assert posts["total"] == 129 and len(posts["items"]) == 5
    c = posts["counts_by_status"]
    assert c["above"] + c["as_expected"] + c["below"] + c["provisional"] + c["no_forecast"] == 129
    matured = call(server, "get_posts", status="as_expected", limit=1)["items"][0]
    one = call(server, "get_post", post_id=matured["id"])
    assert one["post"]["score"]["label"] == "as_expected" and "frozen forecast said" in one["summary"]
    missing = call(server, "get_post", post_id="post:99999")
    assert missing["post"] is None


def test_get_forecast_frozen_and_draft(server: Any, mcp_settings: Settings) -> None:
    frozen = call(server, "get_forecast", subject="post:1")
    assert frozen["forecast"]["frozen"] is True and frozen["forecast"]["id"].startswith("forecast:")
    engine = make_engine(mcp_settings)
    with session_factory(engine)() as s:
        before = s.scalar(select(func.count(Forecast.id)))
    when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    draft = call(server, "get_forecast", planned_at=when, text="A brand new idea about forecasting reach")
    assert draft["forecast"]["frozen"] is False and draft["forecast"]["quantiles"]["q50"] >= 0
    with session_factory(engine)() as s:
        assert s.scalar(select(func.count(Forecast.id))) == before  # nothing frozen


def test_register_planned_post_freezes_and_guards(server: Any, mcp_settings: Settings) -> None:
    when = datetime.now(UTC) + timedelta(days=2)
    ok = call(
        server,
        "register_planned_post",
        planned_at=when.isoformat(),
        text="Totally fresh text about SQLite WAL",
    )
    assert ok["registered"] and ok["slot"].startswith("slot:") and ok["forecast"]["frozen"]
    close = call(server, "register_planned_post", planned_at=(when + timedelta(hours=1)).isoformat())
    assert not close["registered"] and close["guardrails"][0]["rule"] == "original_spacing"
    forced = call(
        server,
        "register_planned_post",
        planned_at=(when + timedelta(hours=1)).isoformat(),
        accept_warnings=True,
    )
    assert forced["registered"]
    past = call(
        server, "register_planned_post", planned_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()
    )
    assert (
        not past["registered"] and "future" in past["summary"]
    )  # live forecasts can't be made after the fact
    schedule = call(server, "get_schedule")
    assert [s["state"] for s in schedule["slots"]] == ["planned", "planned"]
    assert schedule["slots"][0]["forecast_q50"] is not None


def test_set_label_validates_and_records_owner_labels(server: Any, mcp_settings: Settings) -> None:
    bad = call(server, "set_label", post_id="post:1", field="topic", value="cooking")
    assert not bad["saved"] and "isn't a topic" in bad["summary"]
    good = call(server, "set_label", post_id="post:1", field="topic", value="ai")
    assert good["saved"]
    detail = call(server, "get_post", post_id="post:1")
    topic = next(lb for lb in detail["post"]["labels"] if lb["field"] == "topic")
    assert (topic["value"], topic["source"]) == ("ai", "owner")


def test_account_overview_and_export(server: Any) -> None:
    ov = call(server, "get_account_overview")
    assert "followers" in ov["summary"] and ov["overview"]["total_posts"] == 129
    # Past results against expectations are always part of the account view, not only forecasts.
    assert any("expected" in line and "Impressions, last 7 days" in line for line in ov["scorecard"])
    tile = ov["overview"]["tiles"]["impressions_7d"]["expected"]
    assert tile["outcome"] in ("above", "as_expected", "below")
    exported = call(server, "export_data", what="all", fmt="csv")
    assert len(exported["files"]) == 3
    assert all(Path(f).exists() and Path(f).stat().st_size > 0 for f in exported["files"])


def test_resource_and_prompts(server: Any) -> None:
    contents = list(asyncio.run(server.read_resource("socialmgr://briefing/latest")))
    assert "reached 48h" in str(contents[0].content)
    prompts = {p.name for p in asyncio.run(server.list_prompts())}
    assert prompts == {"weekly_review", "plan_next_week"}


def test_confidence_thresholds() -> None:
    from socialmgr.services.agent import confidence_for

    assert confidence_for(5) == "too early"
    assert confidence_for(30) == "suggestive"
    assert confidence_for(80) == "solid"
    assert confidence_for(80, calibrated=False) == "too early"


def test_experiment_tools_end_to_end_and_no_agent_approval(server: Any) -> None:
    listed = call(server, "get_experiments")
    keys = {sg["key"] for sg in listed["suggestions"]}
    assert {"E01", "E03", "E09"} <= keys and "E10" not in keys
    power = call(server, "power_check", suggestion_key="E03")
    assert set(power["power"]["power"]) == {"1.25x", "1.5x", "2x"}

    proposed = call(server, "propose_experiment", suggestion_key="E03")
    assert proposed["experiment"]["status"] == "scheduled", proposed["summary"]

    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    slots = call(
        server,
        "register_slots",
        days=[{"day": tomorrow, "slots": [{"time": "07:00"}, {"time": "11:00"}, {"time": "16:00"}]}],
    )
    assert len(slots["slots"]) == 3, slots["summary"]
    detail = call(server, "get_experiment", experiment="E03")
    assert detail["experiment"]["key"] == "E03"
    readout = call(server, "get_readout", experiment="E03")
    assert readout["readout"] is None and "first look" in readout["summary"]

    # Amending a frozen field creates an approval; there is no tool an agent could approve it with.
    amended = call(server, "amend_experiment", experiment="E03", changes={"delta": 0.2}, reason="test")
    assert len(amended["approvals"]) == 1, amended["summary"]
    pending = call(server, "list_approvals")
    assert [a["id"] for a in pending["approvals"]] == [amended["approvals"][0]["id"]]
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert not any("approv" in name and name != "list_approvals" for name in tools)

    stopped = call(server, "stop_experiment", experiment="E03", reason="done testing")
    assert stopped["experiment"]["status"] == "stopped"
