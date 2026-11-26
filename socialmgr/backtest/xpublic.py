# ruff: noqa: E501  (report strings)
"""Read other accounts' public posts for the external backtest, with the owner's own X access.

Every response is cached under ``<data>/backtest/raw`` so a re-run never pays twice, and every
billable read is recorded as a cost in the install's database (provider ``x_backtest``), so it
counts against the monthly cap and shows in the spend. A per-command budget stops a pull before it
can exceed what the owner approved.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Cost
from socialmgr.providers.x.api import XApi
from socialmgr.providers.x.provider import TWEET_FIELDS, DbTokenSource
from socialmgr.services import pipeline

POST_READ_USD = 0.005  # X API pay-per-use: a post read (not the owner's own)
USER_READ_USD = 0.010  # a user lookup
PUBLIC_TWEET_FIELDS = ",".join(
    f for f in TWEET_FIELDS.split(",") if f not in ("non_public_metrics", "organic_metrics")
)
USER_FIELDS = "public_metrics,created_at,description,verified"


class BudgetExceeded(RuntimeError):
    pass


class PublicReader:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        budget_usd: float,
        api: XApi | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings
        self.budget = budget_usd
        self.spent = 0.0
        self.clock = clock
        self.raw = settings.data_dir / "backtest" / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self._api = api

    # ------------------------------------------------------------ plumbing

    def api(self) -> XApi:
        if self._api is None:
            owner = pipeline.owner_account(self.session, self.settings.workspace_id)
            if owner is None:
                raise RuntimeError(
                    "Connect your X account first (socialmgr connect): the backtest reads with it."
                )
            http = httpx.Client(timeout=30.0)
            token = DbTokenSource(self.settings, http)(owner.id)
            self._api = XApi(http, self.settings.x.api_base, token)
        return self._api

    def _cache(self, kind: str, params: dict[str, Any]) -> Path:
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
        return self.raw / f"{kind}-{key}.json"

    def _charge(self, item: str, n: int, unit: float) -> None:
        amount = round(n * unit, 6)
        if amount <= 0:
            return
        self.spent += amount
        self.session.add(
            Cost(
                workspace_id=self.settings.workspace_id,
                run_id=None,
                provider="x_backtest",
                item=item,
                amount_usd=amount,
                simulated=False,
                incurred_at=self.clock(),
            )
        )
        self.session.commit()

    def _guard(self, projected: float) -> None:
        if self.spent + projected > self.budget + 1e-9:
            raise BudgetExceeded(
                f"This would spend ${self.spent + projected:.2f}, over the ${self.budget:.2f} budget for this "
                "command. Raise --budget to continue."
            )
        month = pipeline.month_spend(self.session, self.settings.workspace_id, self.clock())
        if month + projected > self.settings.monthly_cap_usd + 1e-9:
            raise BudgetExceeded(
                f"This would take the month's X spend to ${month + projected:.2f}, over the "
                f"${self.settings.monthly_cap_usd:.2f} cap (SOCIALMGR_MONTHLY_CAP_USD)."
            )

    def _get(
        self, kind: str, path: str, params: dict[str, Any], projected: float
    ) -> tuple[dict[str, Any], bool]:
        cached = self._cache(kind, {"path": path, **params})
        if cached.exists():
            return json.loads(cached.read_text()), True
        self._guard(projected)
        body = self.api().get(path, params, key=kind).body
        cached.write_text(json.dumps(body))
        return body, False

    # ------------------------------------------------------------ reads

    def users(self, handles: list[str]) -> list[dict[str, Any]]:
        """Profiles with follower counts. $0.01 per account returned."""
        names = ",".join(sorted(h.lstrip("@") for h in handles))
        body, hit = self._get(
            "users",
            "/2/users/by",
            {"usernames": names, "user.fields": USER_FIELDS},
            USER_READ_USD * len(handles),
        )
        data = list(body.get("data") or [])
        if not hit:
            self._charge("user_read", len(data), USER_READ_USD)
        return data

    def search_authors(self, query: str, max_results: int = 25) -> list[dict[str, Any]]:
        """Authors of recent posts matching ``query``, with follower counts (to find small accounts)."""
        n = max(10, min(max_results, 100))
        params: dict[str, Any] = {
            "query": query,
            "max_results": n,
            "expansions": "author_id",
            "user.fields": USER_FIELDS,
            "tweet.fields": "created_at,public_metrics",
        }
        projected = n * (POST_READ_USD + USER_READ_USD)
        body, hit = self._get("search", "/2/tweets/search/recent", params, projected)
        posts = body.get("data") or []
        users = (body.get("includes") or {}).get("users") or []
        if not hit:
            self._charge("post_read", len(posts), POST_READ_USD)
            self._charge("user_read", len(users), USER_READ_USD)
        return list(users)

    def timeline(self, user_id: str, n_posts: int) -> list[dict[str, Any]]:
        """The account's most recent ``n_posts`` posts (reposts excluded, so not billed), newest first."""
        out: list[dict[str, Any]] = []
        token: str | None = None
        while len(out) < n_posts:
            params: dict[str, Any] = {
                "max_results": 100,
                "exclude": "retweets",
                "tweet.fields": PUBLIC_TWEET_FIELDS,
                "expansions": "attachments.media_keys",
                "media.fields": "type",
            }
            if token:
                params["pagination_token"] = token
            body, hit = self._get(
                f"timeline-{user_id}", f"/2/users/{user_id}/tweets", params, 100 * POST_READ_USD
            )
            data = list(body.get("data") or [])
            if not hit:
                self._charge("post_read", len(data), POST_READ_USD)
            media = {
                m["media_key"]: m.get("type", "") for m in (body.get("includes") or {}).get("media") or ()
            }
            for t in data:
                t["_media_types"] = media
            out += data
            token = (body.get("meta") or {}).get("next_token")
            if not token or not data:
                break
        return out[:n_posts]
