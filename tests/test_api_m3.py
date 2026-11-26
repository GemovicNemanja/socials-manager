"""Contract tests for the M3 routes: model health, alarms, followers and goals."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from socialmgr.api.app import create_app
from socialmgr.core.config import PluginConfig, RunConfig
from tests.conftest import make_settings


def _client(tmp_path: Path, fixture_db: Path) -> TestClient:
    import shutil

    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_db, settings.db_path)
    return TestClient(create_app(settings, web_dist=None, regime_fetcher=None))


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def test_model_health_reads_calibration_and_monitors(tmp_path: Path, fixture_db: Path) -> None:
    api = _client(tmp_path, fixture_db)
    m = ok(api.get("/api/model"))
    assert m["owner_n"] > 60 and m["owner_terms_at"] == 20
    assert m["hit_80"]["n"] == m["pit"]["n"] <= 63 and len(m["pit"]["counts"]) == 10
    assert {c["level"] for c in m["coverage"]} == {0.5, 0.8, 0.95}
    assert {mon["chart"] for mon in m["monitors"]} == {"d1_location", "d1_scale", "d1_followers", "d2", "d3"}
    assert m["drift"]["armed"] is True and m["drift"]["threshold"] == 7.62
    assert any(c["source"] == "X ranker repo" for c in m["changes"])
    assert m["card"]["champion"] == "nb2" and m["stage"] == "prior_only"


def test_ack_unknown_alarm_is_refused(tmp_path: Path, fixture_db: Path) -> None:
    api = _client(tmp_path, fixture_db)
    out = ok(api.post("/api/alarms/99999/ack", json={"note": "x"}))
    assert out["acknowledged"] is False and "no alarm" in out["summary"]


def test_followers_view_has_actuals_funnel_and_tiles(tmp_path: Path, fixture_db: Path) -> None:
    api = _client(tmp_path, fixture_db)
    f = ok(api.get("/api/followers"))
    assert len(f["actual"]) > 20 and f["intervals_n"] == len(f["actual"]) - 1
    assert f["tiles"]["followers"]["value"] == f["actual"][-1]["value"]
    assert f["tiles"]["new_follows_7d"]["value"] > 0 and f["tiles"]["unfollows_7d"]["value"] >= 0
    keys = [s["key"] for s in f["funnel"]]
    assert keys == ["impressions", "profile_visits", "new_follows", "unfollows", "net"]


def test_goals_set_supersede_and_validate(tmp_path: Path, fixture_db: Path) -> None:
    api = _client(tmp_path, fixture_db)
    assert ok(api.get("/api/goals"))["current"] == []
    when = (datetime.now(UTC) + timedelta(days=60)).isoformat()
    first = ok(api.post("/api/goals", json={"scope": "followers", "target": 1000, "target_at": when}))
    assert first["saved"] and first["goal"]["scope"] == "followers"
    second = ok(api.post("/api/goals", json={"scope": "followers", "target": 1200, "target_at": when}))
    assert second["saved"]
    goals = ok(api.get("/api/goals"))
    assert [c["goal"]["target"] for c in goals["current"]] == [1200]
    assert len(goals["history"]) == 2 and goals["history"][1]["superseded_at"] is not None
    bad = ok(api.post("/api/goals", json={"scope": "followers", "target": 1000}))
    assert not bad["saved"] and "date" in bad["summary"]
    posts = ok(api.post("/api/goals", json={"scope": "posts", "target": 900}))
    assert posts["saved"]
    preview = ok(
        api.post("/api/goals/preview", json={"scope": "followers", "target": 900, "target_at": when})
    )
    assert preview["status"] in ("working", "near", "not_working", "no_goal")
