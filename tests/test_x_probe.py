"""Day-1 probes: follower-order verdicts and the report written to data_dir/probes/."""

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.providers.x.errors import ReauthRequired
from socialmgr.providers.x.probe import follower_order_verdict, run_probe, shape
from tests.x_fakes import NOW, OWN_ID, FakeX, me_body, respond, seed_owner, tweet, x_settings

TL = f"/2/users/{OWN_ID}/tweets"
FL = f"/2/users/{OWN_ID}/followers"


@pytest.mark.parametrize(
    ("previous", "current", "verdict"),
    [
        (None, ["a", "b"], "undetermined"),
        (["a", "b", "c"], ["n2", "n1", "a", "b", "c"], "newest_first"),
        (["a", "b", "c"], ["n1", "a", "c", "old"], "newest_first"),  # b unfollowed; tail beyond sample
        (["a", "b", "c"], ["a", "b", "c"], "undetermined"),
        (["a", "b", "c"], ["a", "n1", "b", "c"], "not_newest_first"),
        (["a", "b", "c"], ["n1", "b", "a", "c"], "not_newest_first"),
        (["a", "b"], ["x", "y"], "undetermined"),
    ],
)
def test_follower_order_verdict(previous: list[str] | None, current: list[str], verdict: str) -> None:
    assert follower_order_verdict(previous, current)["verdict"] == verdict


def test_shape_hides_values() -> None:
    assert shape({"data": [{"id": "1", "n": 2}, {"id": "2", "n": 3}]}) == {
        "data": [{"id": "str", "n": "int"}, "... 2 item(s)"]
    }


def _fake(followers: list[str], analytics: dict[str, Any], analytics_status: int = 200) -> FakeX:
    old_post = tweet("50", NOW - timedelta(days=9))
    return (
        FakeX()
        .on("/2/users/me", respond(me_body()))
        .on(
            TL,
            respond({"data": [tweet("60", NOW - timedelta(hours=3))], "meta": {}}, remaining=1499),
            respond({"data": [old_post], "meta": {}}, remaining=1498),
        )
        .on(FL, respond({"data": [{"id": f} for f in followers], "meta": {}}))
        .on("/2/tweets/analytics", respond(analytics, status=analytics_status))
    )


def test_probe_report_and_second_probe(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    seed_owner(factory)
    analytics = {
        "data": [
            {
                "id": "50",
                "timestamped_metrics": [
                    {"timestamp": "2026-11-16T06:00:00Z", "metrics": {"impressions": 10}}
                ],
            }
        ]
    }
    fake = _fake(["b", "a"], analytics)
    report, path = run_probe(s, factory, http=fake.client(), clock=lambda: NOW)

    assert path.parent == s.data_dir / "probes" and path.exists()
    saved = json.loads(path.read_text())
    assert saved == report
    assert "access-1" not in path.read_text() and "refresh-1" not in path.read_text()
    assert report["timeline_rate_limit"]["remaining"] == 1499
    assert report["timeline_rate_limit"]["headers"]["x-rate-limit-limit"] == "900"
    assert report["follower_order"]["verdict"] == "undetermined"
    assert report["follower_order"]["sample_ids"] == ["b", "a"]
    assert _q(fake, FL)["max_results"] == "200"

    a = report["analytics"]
    assert a["available"] is True and a["status"] == 200
    assert a["request"]["params"]["ids"] == "50"
    assert a["request"]["params"]["granularity"] == "hourly"
    assert a["shape"]["data"][0]["timestamped_metrics"][0]["metrics"] == {"impressions": "int"}
    older_call = [c for c in fake.calls if c.url.path == TL][1]
    assert older_call.url.params["end_time"] == "2026-11-18T06:00:00Z"  # now - 7 days

    # A day later, one new follower on top of the known ones.
    later = NOW + timedelta(days=1)
    fake2 = _fake(["c", "b", "a"], {"errors": [{"title": "Forbidden"}]}, analytics_status=403)
    fake2.on(
        "/2/oauth2/token", respond({"access_token": "access-2", "refresh_token": "r2", "expires_in": 7200})
    )
    report2, path2 = run_probe(s, factory, http=fake2.client(), clock=lambda: later)
    assert fake2.paths()[0] == "/2/oauth2/token"  # the day-old access token was refreshed first
    assert report2["credential"]["days_since_refresh_before_probe"] == pytest.approx(1.04, abs=0.01)
    assert path2 != path
    assert report2["follower_order"]["compared_with"] == path.name
    assert report2["follower_order"]["verdict"] == "newest_first"
    assert report2["analytics"]["available"] is False and report2["analytics"]["status"] == 403


def test_probe_without_connection(settings: Settings, factory: sessionmaker[Session]) -> None:
    with pytest.raises(ReauthRequired, match="socialmgr connect"):
        run_probe(x_settings(settings), factory, http=FakeX().client(), clock=lambda: NOW)


def _q(fake: FakeX, path: str) -> httpx.QueryParams:
    return next(c for c in fake.calls if c.url.path == path).url.params
