"""Contract tests for the briefing, run and planned-post routes (the web side of the agent services).

Every settings object here uses labeler "none", no regime check and no backup, and the app gets a
regime fetcher of None, so nothing touches the network. Forecast numbers depend on the champion
model and are checked as invariants.
"""

import dataclasses
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from socialmgr.api import schemas
from socialmgr.api.app import create_app
from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.db import migrate
from socialmgr.db.models import Forecast, PlannedSlot, Run
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.labelling.taxonomy import FORMATS, TOPICS
from socialmgr.providers.x.errors import ReauthRequired
from socialmgr.services import agent, pipeline, runs
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings

ENVELOPE = {"summary", "confidence", "as_of", "model_version", "regime_id", "data_sufficiency", "caveats"}


def offline_settings(tmp_path: Path) -> Settings:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


@pytest.fixture
def fx_settings(tmp_path: Path, fixture_db: Path) -> Settings:
    settings = offline_settings(tmp_path)
    shutil.copy(fixture_db, settings.db_path)
    return settings


@pytest.fixture
def empty_settings(tmp_path: Path) -> Settings:
    settings = offline_settings(tmp_path)
    migrate.upgrade(make_engine(settings))
    return settings


def make_client(settings: Settings) -> TestClient:
    plugins = dataclasses.replace(load_plugins(settings), labeler=NoLabeler())
    return TestClient(create_app(settings, web_dist=None, plugins=plugins, regime_fetcher=None))


@pytest.fixture
def api(fx_settings: Settings) -> TestClient:
    return make_client(fx_settings)


def ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def count(settings: Settings, model: Any) -> int:
    with session_factory(make_engine(settings))() as s:
        return int(s.scalar(select(func.count(model.id))) or 0)


# ---------------------------------------------------------------- briefing


def test_briefing_is_typed_and_quotes_record_ids(api: TestClient) -> None:
    body = ok(api.get("/api/briefing"))
    assert set(body) >= ENVELOPE | {"run_id", "lines", "briefing"}
    assert body["run_id"] == 43
    assert "reached 48h" in body["lines"][0] and body["lines"] == body["summary"].split("\n")
    data = body["briefing"]
    assert data["run_id"] == 43 and data["new_posts"] == 3
    assert sum(data["scored"].values()) == 3
    assert all(n["post"].startswith("post:") and n["label"] != "as_expected" for n in data["notable"])
    assert all(
        d["post"].startswith("post:") and d["matches"].startswith("post:") for d in data["near_duplicates"]
    )
    assert data["spend"]["cap_usd"] == 15.0 and 0 <= data["spend"]["month_usd"] <= 15.0
    assert body["confidence"] == "too early"  # prior-only champion


def test_briefing_day_zero(empty_settings: Settings) -> None:
    body = ok(make_client(empty_settings).get("/api/briefing"))
    assert body["run_id"] is None and body["briefing"] is None
    assert set(body) >= ENVELOPE


# ---------------------------------------------------------------- runs


def test_run_status_on_the_fixture(api: TestClient) -> None:
    body = ok(api.get("/api/runs/status"))
    assert set(body) >= ENVELOPE | {"latest", "unfinished", "active", "step_summaries", "failure"}
    assert body["active"] is False and body["unfinished"] is None and body["failure"] is None
    assert [s["name"] for s in body["latest"]["steps"]] == runs.STEPS.names
    assert set(body["step_summaries"]) == set(runs.STEPS.names)
    assert "new posts" in body["step_details"]["collect"]


def test_start_run_returns_the_new_briefing(api: TestClient) -> None:
    body = ok(api.post("/api/runs"))
    assert body["briefing"]["run_id"] == 44 and body["briefing"]["briefing"]["run_id"] == 44
    status = body["status"]
    assert status["latest"]["run_id"] == 44 and status["latest"]["status"] == "finished"
    assert status["unfinished"] is None and status["failure"] is None and status["active"] is False
    assert ok(api.get("/api/briefing"))["run_id"] == 44


def test_start_run_while_another_holds_the_lock(api: TestClient, fx_settings: Settings) -> None:
    with runs.run_lock(fx_settings):
        body = ok(api.post("/api/runs"))
    assert body["briefing"]["run_id"] is None and "in progress" in body["briefing"]["summary"]
    assert ok(api.get("/api/briefing"))["run_id"] == 43  # nothing ran


def test_failed_collection_is_classified_then_resumes(
    api: TestClient, fx_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def expired(*_: object, **__: object) -> None:
        raise ReauthRequired("X rejected the refresh token.")

    with monkeypatch.context() as m:
        m.setattr(pipeline, "collect", expired)
        body = ok(api.post("/api/runs"))
    failure = body["status"]["failure"]
    assert failure["kind"] == "reauth" and failure["step"] == "collect"
    assert failure["command"] == "socialmgr connect" and "refresh token" in failure["detail"]
    assert body["status"]["unfinished"]["status"] == "failed"
    assert ok(api.get("/api/runs/status"))["failure"]["kind"] == "reauth"

    resumed = ok(api.post("/api/runs"))
    assert resumed["status"]["failure"] is None and resumed["status"]["latest"]["status"] == "finished"
    assert resumed["briefing"]["run_id"] == body["status"]["unfinished"]["run_id"]


def _status(state: str, steps: dict[str, tuple[str, str | None]], started: datetime) -> agent.RunStatus:
    return agent.RunStatus(
        run_id=7,
        status=state,
        trigger="web",
        started_at=started,
        cutoff_at=started,
        finished_at=None,
        spend_usd=0.0,
        steps=[
            agent.StepStatus(
                name=n, status=steps.get(n, ("pending", None))[0], error=steps.get(n, ("", None))[1]
            )
            for n in runs.STEPS.names
        ],
    )


def test_classify_failure_kinds() -> None:
    now = datetime.now(UTC)
    unavailable = _status("failed", {"collect": ("failed", "XUnavailable: timed out")}, now)
    assert schemas.classify_failure(unavailable, active=False).kind == "x_unavailable"  # type: ignore[union-attr]
    other = schemas.classify_failure(
        _status("failed", {"collect": ("done", None), "label": ("failed", "KeyError: 'x'")}, now), False
    )
    assert other is not None and other.kind == "other" and other.step == "label" and other.command is None
    running = _status("running", {"collect": ("done", None)}, now)
    assert schemas.classify_failure(running, active=True) is None
    stale = schemas.classify_failure(running, active=False)
    assert stale is not None and stale.kind == "interrupted" and stale.step == "regimes"


def test_a_recent_running_run_from_another_process_counts_as_active(
    api: TestClient, fx_settings: Settings
) -> None:
    engine = make_engine(fx_settings)
    with session_factory(engine)() as s:
        run_id = pipeline.start_run(
            s, fx_settings, trigger="cli", cutoff=datetime.now(UTC), now=datetime.now(UTC)
        ).id
        s.commit()
    body = ok(api.get("/api/runs/status"))
    assert body["active"] is True and body["failure"] is None
    with session_factory(engine)() as s:
        run = s.get(Run, run_id)
        assert run is not None
        run.started_at = datetime.now(UTC) - schemas.RUN_STALE_AFTER - timedelta(minutes=1)
        s.commit()
    engine.dispose()
    body = ok(api.get("/api/runs/status"))
    assert body["active"] is False and body["failure"]["kind"] == "interrupted"


# ---------------------------------------------------------------- planned posts


def test_label_choices_match_the_taxonomy() -> None:
    assert get_args(schemas.PostFormat) == FORMATS
    assert get_args(schemas.Topic) == TOPICS


def test_draft_freezes_nothing_and_reports_guardrails(api: TestClient, fx_settings: Settings) -> None:
    before = count(fx_settings, Forecast)
    when = datetime.now(UTC) + timedelta(days=1)
    body = ok(
        api.post(
            "/api/planned/draft",
            json={
                "planned_at": when.isoformat(),
                "text": "A fresh idea about range forecasts",
                "topic": "ai",
            },
        )
    )
    assert set(body) >= ENVELOPE | {"forecast", "guardrails"}
    fc = body["forecast"]
    assert fc["frozen"] is False and fc["id"] is None and fc["kind"] == "draft"
    assert fc["quantiles"]["q10"] <= fc["quantiles"]["q50"] <= fc["quantiles"]["q90"]
    assert count(fx_settings, Forecast) == before and count(fx_settings, PlannedSlot) == 0

    hashtags = ok(api.post("/api/planned/draft", json={"planned_at": when.isoformat(), "text": "#one #two"}))
    assert any(g["rule"] == "hashtags" and g["severity"] == "warn" for g in hashtags["guardrails"])


def test_register_freezes_then_lists_and_guards(api: TestClient, fx_settings: Settings) -> None:
    when = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    body = ok(
        api.post(
            "/api/planned",
            json={
                "planned_at": when.isoformat(),
                "text": "Registering from the web app",
                "post_format": "Text",
            },
        )
    )
    assert body["registered"] is True and body["slot"].startswith("slot:")
    assert body["forecast"]["frozen"] is True and body["forecast"]["id"].startswith("forecast:")

    close = (when + timedelta(hours=1)).isoformat()
    refused = ok(api.post("/api/planned", json={"planned_at": close}))
    assert refused["registered"] is False and refused["guardrails"][0]["rule"] == "original_spacing"
    forced = ok(api.post("/api/planned", json={"planned_at": close, "accept_warnings": True}))
    assert forced["registered"] is True

    past = ok(
        api.post("/api/planned", json={"planned_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()})
    )
    assert past["registered"] is False and "future" in past["summary"]

    schedule = ok(api.get("/api/planned"))
    assert set(schedule) >= ENVELOPE | {"slots"}
    slots = schedule["slots"]
    assert [s["slot"] for s in slots] == [body["slot"], forced["slot"]]
    assert slots[0]["state"] == "planned" and slots[0]["text"] == "Registering from the web app"
    assert datetime.fromisoformat(slots[0]["planned_at"]) == when
    assert slots[0]["forecast_q50"] == body["forecast"]["quantiles"]["q50"]


def test_planned_requests_are_validated(api: TestClient) -> None:
    naive = (datetime.now() + timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert api.post("/api/planned/draft", json={"planned_at": naive}).status_code == 422
    when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (
        api.post("/api/planned/draft", json={"planned_at": when, "post_format": "Essay"}).status_code == 422
    )
    assert api.post("/api/planned", json={"planned_at": when, "post_type": "tweet"}).status_code == 422
    assert api.post("/api/planned", json={"planned_at": when, "extra": 1}).status_code == 422


def test_planned_day_zero(empty_settings: Settings) -> None:
    client = make_client(empty_settings)
    when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    draft = client.post("/api/planned/draft", json={"planned_at": when})
    assert draft.status_code == 409 and "run" in draft.json()["detail"]
    assert ok(client.get("/api/planned"))["slots"] == []
