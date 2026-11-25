from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.api.app import create_app
from socialmgr.core.config import Settings


def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, web_dist=None))


def test_health(settings: Settings, loaded: sessionmaker[Session]) -> None:
    assert client(settings).get("/api/health").json()["status"] == "ok"


def test_shell(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = client(settings).get("/api/shell").json()
    assert body["account"]["handle"] == "@your_account"
    assert body["account"]["initials"] == "YA"
    assert body["last_run"]["status"] == "finished"
    assert body["counts"]["posts"] == 129
    assert body["spend"]["cap_usd"] == 15.0


def test_posts_envelope_and_items(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = client(settings).get("/api/posts", params={"limit": 5}).json()
    assert set(body) >= {"as_of", "model_version", "data_sufficiency", "caveats", "total", "items"}
    assert body["total"] == 129 and len(body["items"]) == 5
    assert sum(body["counts_by_status"].values()) == 129
    item = body["items"][0]
    assert item["id"].startswith("post:")
    assert item["status"] in {"above", "as_expected", "below", "provisional", "no_forecast"}
    created = [i["created_at"] for i in body["items"]]
    assert created == sorted(created, reverse=True)


def test_posts_status_filter(settings: Settings, loaded: sessionmaker[Session]) -> None:
    body = client(settings).get("/api/posts", params={"status": "above", "limit": 500}).json()
    assert body["items"] and all(i["status"] == "above" for i in body["items"])
    assert all(i["pit_mid"] >= 0.8 for i in body["items"])


def test_bad_status_is_rejected(settings: Settings, loaded: sessionmaker[Session]) -> None:
    assert client(settings).get("/api/posts", params={"status": "working"}).status_code == 422
