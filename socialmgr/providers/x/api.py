"""A thin X API v2 client: bearer auth, JSON, and the rate-limit headers of every response."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from socialmgr.providers.x.errors import ReauthRequired, XApiError

RATE_LIMIT_HEADERS = ("x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset")


@dataclass(frozen=True)
class RateLimit:
    limit: int | None
    remaining: int | None
    reset_at: datetime | None

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> "RateLimit":
        def num(name: str) -> int | None:
            raw = headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        reset = num("x-rate-limit-reset")
        return cls(
            limit=num("x-rate-limit-limit"),
            remaining=num("x-rate-limit-remaining"),
            reset_at=datetime.fromtimestamp(reset, UTC) if reset is not None else None,
        )

    def describe(self) -> str:
        reset = self.reset_at.isoformat() if self.reset_at else "unknown"
        return f"remaining {self.remaining}/{self.limit}, resets {reset}"


class RateLimited(Exception):
    """A 429. Callers stop paging and keep what they have; the rest carries to the next run."""

    def __init__(self, path: str, rate: RateLimit) -> None:
        super().__init__(f"rate limited on {path} ({rate.describe()})")
        self.path = path
        self.rate = rate


@dataclass(frozen=True)
class Page:
    body: dict[str, Any]
    rate: RateLimit
    status: int
    headers: dict[str, str]


class XApi:
    def __init__(self, http: httpx.Client, base_url: str, access_token: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self._auth = {"Authorization": f"Bearer {access_token}"}
        self.rate_limits: dict[str, RateLimit] = {}

    def raw_get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return self.http.get(f"{self.base_url}{path}", params=params, headers=self._auth)

    def get(self, path: str, params: dict[str, Any] | None = None, *, key: str | None = None) -> Page:
        """GET a JSON resource. ``key`` names the endpoint for rate-limit bookkeeping."""
        response = self.raw_get(path, params)
        rate = RateLimit.from_headers(response.headers)
        self.rate_limits[key or path] = rate
        if response.status_code == 429:
            raise RateLimited(path, rate)
        if response.status_code == 401:
            raise ReauthRequired("X rejected the access token (401).")
        if response.status_code != 200:
            raise XApiError(response.status_code, path, response.text)
        headers = {h: response.headers[h] for h in RATE_LIMIT_HEADERS if h in response.headers}
        return Page(body=response.json(), rate=rate, status=response.status_code, headers=headers)
