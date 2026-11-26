"""External backtest tooling, offline: a fake X API stands in for the real one."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.backtest import runner
from socialmgr.backtest.xpublic import BudgetExceeded, PublicReader
from socialmgr.core.config import Settings
from socialmgr.db.models import Cost
from socialmgr.providers.x.api import Page, RateLimit

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _timeline(user_id: str, n: int, level: float, seed: int = 1) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        created = NOW - timedelta(days=4 + i * 0.5)
        reply = i % 5 == 0
        out.append(
            {
                "id": str(10_000 + i),
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "text": f"post {i}",
                "author_id": user_id,
                "public_metrics": {
                    "impression_count": int(rng.lognormal(np.log(level * (0.2 if reply else 1)), 0.8))
                },
                **(
                    {"in_reply_to_user_id": "999", "referenced_tweets": [{"type": "replied_to", "id": "1"}]}
                    if reply
                    else {}
                ),
            }
        )
    return out


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.users = {
            "small": {
                "id": "1",
                "username": "small",
                "public_metrics": {"followers_count": 600, "tweet_count": 900},
            },
            "big": {
                "id": "2",
                "username": "big",
                "public_metrics": {"followers_count": 250_000, "tweet_count": 9_000},
            },
        }

    def get(self, path: str, params: dict[str, Any] | None = None, *, key: str | None = None) -> Page:
        self.calls.append(path)
        rate = RateLimit(None, None, None)
        p = params or {}
        if path == "/2/users/by":
            names = str(p["usernames"]).split(",")
            return Page({"data": [self.users[n] for n in names if n in self.users]}, rate, 200, {})
        uid = path.split("/")[3]
        level = 300 if uid == "1" else 40_000
        tweets = _timeline(uid, 200, level, seed=int(uid))
        start = 100 if p.get("pagination_token") else 0
        body: dict[str, Any] = {"data": tweets[start : start + 100]}
        if start == 0:
            body["meta"] = {"next_token": "page2"}
        return Page(body, rate, 200, {})


def _reader(loaded: sessionmaker[Session], settings: Settings, budget: float, api: FakeApi) -> PublicReader:
    return PublicReader(loaded(), settings, budget_usd=budget, api=api, clock=lambda: NOW)  # type: ignore[arg-type]


def test_reads_are_billed_once_cached_and_capped(loaded: sessionmaker[Session], settings: Settings) -> None:
    api = FakeApi()
    reader = _reader(loaded, settings, 5.0, api)
    users = reader.users(["@small", "@big"])
    tweets = reader.timeline("1", 200)
    assert len(users) == 2 and len(tweets) == 200
    assert reader.spent == pytest.approx(2 * 0.01 + 200 * 0.005)
    calls = len(api.calls)
    again = _reader(loaded, settings, 5.0, api)
    assert len(again.timeline("1", 200)) == 200 and len(api.calls) == calls and again.spent == 0  # cached
    with loaded() as s:
        assert s.scalar(
            select(func.sum(Cost.amount_usd)).where(Cost.provider == "x_backtest")
        ) == pytest.approx(1.02)
    tight = _reader(loaded, settings, 0.10, api)
    with pytest.raises(BudgetExceeded, match="budget"):
        tight.timeline("2", 200)


def test_runner_scores_accounts_and_reports_by_band(tmp_path: Path) -> None:
    api = FakeApi()
    # 60 posts keep the rolling refits quick; the real run uses 200 per account.
    small = runner.run_account("small", api.users["small"], _timeline("1", 60, 300, seed=1), NOW)
    big = runner.run_account("big", api.users["big"], _timeline("2", 60, 40_000, seed=2), NOW)
    assert small.band == "under 1k" and big.band == "100k+"
    for r in (small, big):
        assert r.posts_used == 60 and r.types == {"original": 48, "reply": 12}
        assert r.prior_cov80 is not None and 0 <= r.prior_cov80 <= 1 and r.prior_median_ratio is not None
        assert r.owner_scored >= 20 and r.owner_cov80 is not None and r.gates
    text = runner.report([small, big], NOW)
    assert "| @small | under 1k |" in text and "| @big | 100k+ |" in text and "## Limits" in text


def test_young_posts_and_missing_impressions_are_left_out() -> None:
    user = {"id": "1", "username": "x", "public_metrics": {"followers_count": 50}}
    tweets = _timeline("1", 10, 100)
    tweets[0]["created_at"] = (NOW - timedelta(hours=30)).isoformat().replace("+00:00", "Z")
    tweets[1]["public_metrics"] = {}
    assert len(runner.examples_from_timeline(tweets, user, NOW)) == 8
