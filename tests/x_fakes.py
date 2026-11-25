"""A scripted stand-in for the X API on ``httpx.MockTransport``. No test touches the network."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings, XConfig
from socialmgr.db.models import Account, Credential

API = "https://api.x.com"
OWN_ID = "1000"
NOW = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)

type Handler = Callable[[httpx.Request], httpx.Response]


class FakeX:
    """Routes requests by path to handlers (a list is served in order, one response per call)."""

    def __init__(self) -> None:
        self.routes: dict[str, Handler | list[Handler]] = {}
        self.calls: list[httpx.Request] = []

    def on(self, path: str, *handlers: Handler) -> "FakeX":
        self.routes[path] = list(handlers) if len(handlers) > 1 else handlers[0]
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        if isinstance(route, list):
            if not route:
                raise AssertionError(f"no more scripted responses for {request.url.path}")
            return route.pop(0)(request)
        return route(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def paths(self) -> list[str]:
        return [c.url.path for c in self.calls]


def respond(
    body: dict[str, Any] | None = None, status: int = 200, remaining: int = 100, reset: int = 1_900_000_000
) -> Handler:
    headers = {
        "x-rate-limit-limit": "900",
        "x-rate-limit-remaining": str(remaining),
        "x-rate-limit-reset": str(reset),
    }
    return lambda request: httpx.Response(status, json=body or {}, headers=headers)


def me_body(followers: int = 1234, username: str = "owner") -> dict[str, Any]:
    return {
        "data": {
            "id": OWN_ID,
            "username": username,
            "name": "Owner",
            "public_metrics": {"followers_count": followers},
        }
    }


def tweet(
    tid: str,
    created_at: datetime,
    *,
    text: str = "hello",
    refs: tuple[str, ...] = (),
    reply_to: str | None = None,
    urls: tuple[dict[str, Any], ...] = (),
    media_keys: tuple[str, ...] = (),
    non_public: bool = True,
    impressions: int = 500,
    note: str | None = None,
) -> dict[str, Any]:
    t: dict[str, Any] = {
        "id": tid,
        "text": text,
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "conversation_id": tid,
        "public_metrics": {
            "retweet_count": 3,
            "reply_count": 2,
            "like_count": 40,
            "quote_count": 1,
            "bookmark_count": 5,
            "impression_count": impressions - 7,
        },
        "edit_history_tweet_ids": [tid],
    }
    if non_public:
        t["non_public_metrics"] = {
            "impression_count": impressions,
            "user_profile_clicks": 9,
            **({"url_link_clicks": 4} if urls else {}),
        }
        t["organic_metrics"] = {"impression_count": impressions}
    if refs:
        t["referenced_tweets"] = [{"type": r, "id": f"9{tid}"} for r in refs]
    if reply_to is not None:
        t["in_reply_to_user_id"] = reply_to
    if urls:
        t["entities"] = {"urls": list(urls)}
    if media_keys:
        t["attachments"] = {"media_keys": list(media_keys)}
    if note:
        t["note_tweet"] = {"text": note}
    return t


def x_settings(settings: Settings, handle: str = "@Owner") -> Settings:
    return settings.model_copy(
        update={
            "x": XConfig(client_id="test-client"),
            "account": settings.account.model_copy(update={"handle": handle}),
        }
    )


def seed_owner(
    factory: sessionmaker[Session],
    *,
    handle: str = "@Owner",
    external_id: str | None = OWN_ID,
    access: str | None = "access-1",
    refresh: str | None = "refresh-1",
    expires_in: timedelta | None = timedelta(hours=2),
) -> int:
    """Create the owner account and (optionally) its X credentials; returns the account id."""
    with factory() as session:
        acct = Account(
            workspace_id=1,
            platform="x",
            handle=handle,
            role="owner",
            external_id=external_id,
            tracked_since=NOW,
        )
        session.add(acct)
        session.flush()
        if access is not None:
            session.add(
                Credential(
                    workspace_id=1,
                    account_id=acct.id,
                    provider="x",
                    access_token=access,
                    refresh_token=refresh,
                    expires_at=NOW + expires_in if expires_in is not None else None,
                    refreshed_at=NOW - timedelta(hours=1),
                )
            )
        session.commit()
        return acct.id
