"""Contract tests for GET /api/overview and GET /api/posts/{id} against the fixture database.

Forecast-derived numbers (status counts, quantiles, percentiles) change with the champion model,
so they are checked as invariants. Counts that come only from the fake provider (posts,
followers, runs) are stable and checked exactly.
"""

from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.api.app import create_app
from socialmgr.core.config import Settings
from socialmgr.db.models import Forecast
from socialmgr.services.overview import FollowerPoint, _followers_at, _mid_rank

ENVELOPE = {"as_of", "model_version", "data_sufficiency", "caveats"}
STATUSES = {"above", "as_expected", "below", "provisional", "no_forecast"}


def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, web_dist=None))


def overview(settings: Settings) -> dict[str, Any]:
    response = client(settings).get("/api/overview")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def assert_range(expected: dict[str, Any]) -> None:
    assert expected["q10"] <= expected["q50"] <= expected["q90"]
    assert 0.0 <= expected["percentile"] <= 1.0
    assert expected["n_posts"] > 0


def test_overview_envelope_and_stable_counts(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = overview(settings)
    assert set(body) >= ENVELOPE
    assert body["as_of"] == "2026-11-25T06:00:00Z"
    assert body["account"]["handle"] == "@your_account"
    assert body["total_posts"] == 129
    assert body["days_tracked"] == 43
    assert body["last_run"]["status"] == "finished"
    assert body["last_run"]["id"].startswith("run:")
    assert body["spend"]["cap_usd"] == 15.0
    assert 0 <= body["spend"]["spent_usd"] <= body["spend"]["cap_usd"]


def test_overview_followers_from_snapshots(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = overview(settings)
    series = body["follower_series"]
    assert len(series) == 43  # one follower snapshot per fixture run
    assert [p["at"] for p in series] == sorted(p["at"] for p in series)
    followers = body["tiles"]["followers"]
    assert followers["now"] == series[-1]["followers"] == 415
    # one snapshot per daily run: 7 and 28 days before the last run
    assert followers["change_7d"] == 415 - series[-8]["followers"]
    assert followers["change_28d"] == 415 - series[-29]["followers"]


def test_overview_kpis_are_consistent(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = overview(settings)
    tiles = body["tiles"]
    posts_7d = tiles["posts_7d"]
    assert posts_7d["value"] == sum(body["status_7d"].values())
    assert tiles["impressions_7d"]["value"] > 0
    assert_range(tiles["impressions_7d"]["expected"])
    median = tiles["median_i48"]
    assert median["n_posts"] == 21
    assert_range(median["expected"])
    share = tiles["profile_visit_share_7d"]
    assert share["value"] == round(share["numerator"] / share["denominator"], 4)
    per_1k = tiles["follows_per_1k_7d"]
    assert per_1k["numerator"] == tiles["followers"]["change_7d"]
    assert per_1k["value"] == round(1000 * per_1k["numerator"] / per_1k["denominator"], 4)


def test_overview_status_counts_and_calibration(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = overview(settings)
    for window in ("status_7d", "status_28d"):
        assert set(body[window]) == STATUSES
    assert all(body["status_28d"][k] >= body["status_7d"][k] for k in STATUSES)
    cal = body["calibration"]
    assert cal["n"] > 0
    assert 0 <= cal["in_50_rate"] <= cal["in_80_rate"] <= 1


def test_overview_recent_posts_reuse_post_items(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = overview(settings)
    recent = body["recent_posts"]
    assert len(recent) == 9
    created = [p["created_at"] for p in recent]
    assert created == sorted(created, reverse=True)
    first_page = client(settings).get("/api/posts", params={"limit": 9}).json()["items"]
    assert recent == first_page


def test_overview_has_no_pending_areas_after_m3(settings: Settings, loaded: sessionmaker[Session]) -> None:
    # Follower forecast, model health and goals shipped in M3; the briefing and planned posts in M1.
    assert overview(settings)["pending_areas"] == []


def test_overview_ignores_slot_forecasts(settings: Settings, loaded: sessionmaker[Session]) -> None:
    before = overview(settings)
    with loaded() as session:
        template = session.scalars(select(Forecast).limit(1)).one()
        session.add(
            Forecast(
                subject="slot:1",
                kind="treatment_blind",
                model_version=template.model_version,
                curve_version=template.curve_version,
                metric="impressions",
                horizon_h=48.0,
                quantiles=template.quantiles,
                input_manifest={},
                manifest_hash="0" * 64,
                draws_hash=template.draws_hash,
                frozen_at=datetime(2026, 11, 25, 7, tzinfo=UTC),
            )
        )
        session.commit()
    after = overview(settings)
    assert after["tiles"] == before["tiles"]
    assert after["status_28d"] == before["status_28d"]


def test_overview_day_zero(settings: Settings, factory: sessionmaker[Session]) -> None:
    body = overview(settings)
    assert set(body) >= ENVELOPE
    assert body["as_of"] is None
    assert body["account"] is None
    assert body["total_posts"] == 0
    assert body["recent_posts"] == []
    assert body["follower_series"] == []
    assert body["last_run"] is None
    assert body["data_sufficiency"]["reason"] == "no_posts"
    tiles = body["tiles"]
    assert tiles["followers"]["now"] is None
    assert tiles["impressions_7d"]["value"] is None and tiles["impressions_7d"]["expected"] is None
    assert tiles["median_i48"]["value"] is None
    assert sum(body["status_28d"].values()) == 0


def test_post_detail_matured(settings: Settings, loaded: sessionmaker[Session]) -> None:
    items = client(settings).get("/api/posts", params={"status": "as_expected", "limit": 1}).json()["items"]
    post_id = items[0]["id"]
    body = client(settings).get(f"/api/posts/{post_id}").json()
    assert set(body) >= ENVELOPE | {"post", "labels", "forecast", "score", "snapshots"}
    assert body["post"] == items[0]
    fc = body["forecast"]
    assert fc["id"].startswith("forecast:")
    assert fc["kind"] in {"live", "as_of", "treatment_blind", "prior_only_uncalibrated"}
    assert len(fc["manifest_hash"]) == 64
    assert fc["model_version"] == body["model_version"]
    q = fc["quantiles"]
    shown = [q[k] for k in ("q05", "q25", "q50", "q75", "q95")]
    assert all(v is not None for v in shown) and shown == sorted(shown)
    assert fc["by_age"] and all(p["q10"] <= p["q50"] <= p["q90"] for p in fc["by_age"])
    score = body["score"]
    assert score["label"] == "as_expected" and 0.2 < score["pit_mid"] < 0.8
    assert score["capture_age_h"] >= 48
    ages = [s["age_h"] for s in body["snapshots"]]
    assert ages and ages == sorted(ages)
    assert {lb["field"] for lb in body["labels"]} >= {"format", "topic"}


def test_post_detail_provisional_has_no_score(settings: Settings, loaded: sessionmaker[Session]) -> None:
    items = client(settings).get("/api/posts", params={"status": "provisional", "limit": 1}).json()["items"]
    body = client(settings).get(f"/api/posts/{items[0]['id']}").json()
    assert body["score"] is None
    assert body["post"]["status"] == "provisional"
    assert all(s["age_h"] < 48 for s in body["snapshots"])


def test_post_detail_accepts_bare_ids_and_404s(settings: Settings, loaded: sessionmaker[Session]) -> None:
    c = client(settings)
    assert c.get("/api/posts/1").json()["post"]["id"] == "post:1"
    assert c.get("/api/posts/post:99999").status_code == 404
    assert c.get("/api/posts/slot:1").status_code == 404
    assert c.get("/api/posts/abc").status_code == 404


def test_mid_rank_and_follower_lookup() -> None:
    samples = np.array([1.0, 2.0, 2.0, 3.0])
    assert _mid_rank(samples, 2.0) == 0.5
    assert _mid_rank(samples, 0.0) == 0.0 and _mid_rank(samples, 9.0) == 1.0
    t0 = datetime(2026, 11, 1, 6, tzinfo=UTC)
    series = [FollowerPoint(at=t0, followers=10), FollowerPoint(at=t0.replace(day=8), followers=17)]
    assert _followers_at(series, t0.replace(day=5)) == 10
    assert _followers_at(series, t0.replace(day=8)) == 17
    assert _followers_at(series, t0.replace(day=1, hour=5)) is None
