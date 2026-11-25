"""The official X API v2 provider for the owner account (IMPLEMENTATION_PLAN.md §5).

One ``collect`` makes, for the owner: ``GET /2/users/me`` (follower count), the owner timeline for
the last 30 days with public, non-public and organic metrics, and the newest-first follower list
paged only until a known follower appears.

Credentials: the provider is built from ``Settings`` alone (the plugin registry's contract). It
asks a ``TokenSource`` for an access token per account; the default ``DbTokenSource`` opens its
own engine on ``settings.db_url`` and refreshes under the token lock (``tokens.py``). It writes
only when a refresh is due, so a run should call ``prepare()`` before it opens its own write
transaction (SQLite has one writer); if it doesn't, a due refresh fails safely with
``TokenStoreBusy`` without spending the refresh token.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.core.types import (
    AccountRef,
    BillableRead,
    Capability,
    CollectionBatch,
    CollectionRequest,
    FetchedAccount,
    FetchedMetrics,
    FetchedPost,
    PostType,
)
from socialmgr.providers.x.api import RateLimited, XApi
from socialmgr.providers.x.errors import ReauthRequired
from socialmgr.providers.x.tokens import fresh_access_token

OWNED_READ_USD = 0.001  # per resource returned (posts, follower records, the user lookup)
TIMELINE_WINDOW = timedelta(days=30)  # non-public metrics exist only for posts under 30 days old
TIMELINE_PAGE = 100
FOLLOWER_PAGE = 1000
FOLLOWER_BASELINE = 100  # first run: a small sample of the newest followers to stop at next time
MAX_FOLLOWER_PAGES = 10
TWEET_FIELDS = (
    "created_at,public_metrics,non_public_metrics,organic_metrics,conversation_id,in_reply_to_user_id,"
    "referenced_tweets,entities,attachments,note_tweet"
)
MEDIA_PRIORITY = ("video", "animated_gif", "photo")
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


# ---------------------------------------------------------------- mapping


def classify(tweet: dict[str, Any], own_id: str) -> PostType:
    refs = {r.get("type") for r in tweet.get("referenced_tweets") or ()}
    if "retweeted" in refs:
        return "repost"
    if "replied_to" in refs or tweet.get("in_reply_to_user_id"):
        return "self_reply" if str(tweet.get("in_reply_to_user_id")) == own_id else "reply"
    if "quoted" in refs:
        return "quote"
    return "original"


def has_external_url(tweet: dict[str, Any]) -> bool:
    """True when the post links out. Media links and links to posts on X (quotes) don't count."""
    for url in (tweet.get("entities") or {}).get("urls") or ():
        if url.get("media_key"):
            continue
        expanded = url.get("expanded_url") or url.get("url") or ""
        parts = urlsplit(expanded)
        if parts.hostname in X_HOSTS and "/status/" in parts.path:
            continue
        if (url.get("display_url") or "").startswith(("pic.x.com", "pic.twitter.com")):
            continue
        return True
    return False


def media_kind(tweet: dict[str, Any], media_types: dict[str, str]) -> str | None:
    keys = (tweet.get("attachments") or {}).get("media_keys") or ()
    kinds = {media_types.get(k) for k in keys}
    return next((k for k in MEDIA_PRIORITY if k in kinds), None)


def post_text(tweet: dict[str, Any]) -> str:
    note = tweet.get("note_tweet") or {}
    return str(note.get("text") or tweet.get("text") or "")


def to_post(tweet: dict[str, Any], own_id: str, account_id: int, media_types: dict[str, str]) -> FetchedPost:
    reply_to = tweet.get("in_reply_to_user_id")
    return FetchedPost(
        account_id=account_id,
        external_id=str(tweet["id"]),
        created_at=_parse_time(tweet["created_at"]),
        type=classify(tweet, own_id),
        text=post_text(tweet),
        conversation_id=tweet.get("conversation_id"),
        in_reply_to_user_id=str(reply_to) if reply_to is not None else None,
        has_url=has_external_url(tweet),
        media_kind=media_kind(tweet, media_types),
    )


def to_metrics(tweet: dict[str, Any], fetched_at: datetime) -> FetchedMetrics:
    public = tweet.get("public_metrics") or {}
    private = tweet.get("non_public_metrics") or {}
    impressions = private.get("impression_count", public.get("impression_count"))
    return FetchedMetrics(
        post_external_id=str(tweet["id"]),
        fetched_at=fetched_at,
        impressions=impressions,
        impressions_source="impression_count",
        likes=public.get("like_count"),
        reposts=public.get("retweet_count"),
        quotes=public.get("quote_count"),
        replies=public.get("reply_count"),
        bookmarks=public.get("bookmark_count"),
        profile_clicks=private.get("user_profile_clicks"),
        link_clicks=private.get("url_link_clicks"),
    )


# ---------------------------------------------------------------- credentials


class TokenSource(Protocol):
    def __call__(self, account_id: int) -> str:
        """A valid access token for the account (refreshed and stored first if needed)."""
        ...


class DbTokenSource:
    """Reads and refreshes credentials in the install's own database, under the token lock."""

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client,
        factory: sessionmaker[Session] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.settings = settings
        self.http = http
        self._factory = factory
        self.clock = clock

    def factory(self) -> sessionmaker[Session]:
        if self._factory is None:
            from socialmgr.db.session import make_engine, session_factory

            self._factory = session_factory(make_engine(self.settings))
        return self._factory

    def __call__(self, account_id: int) -> str:
        return fresh_access_token(self.settings, self.factory(), self.http, account_id, self.clock())


# ---------------------------------------------------------------- provider


class XOwnerProvider:
    name = "x"
    capabilities = frozenset({Capability.OWNER_METRICS, Capability.FOLLOWER_LISTS})
    roles = frozenset({"owner"})

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        tokens: TokenSource | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.settings = settings
        self.http = http or httpx.Client(timeout=30.0)
        self.tokens: TokenSource = tokens or DbTokenSource(settings, self.http, clock=clock)
        self.clock = clock
        self.last_api: XApi | None = None

    def prepare(self, account_id: int) -> None:
        """Refresh the token now if due, so ``collect`` makes no database writes."""
        self.tokens(account_id)

    def api_for(self, account_id: int) -> XApi:
        api = XApi(self.http, self.settings.x.api_base, self.tokens(account_id))
        self.last_api = api
        return api

    def collect(self, request: CollectionRequest) -> CollectionBatch:
        accounts: list[FetchedAccount] = []
        posts: list[FetchedPost] = []
        metrics: list[FetchedMetrics] = []
        caveats: list[str] = []
        resources: list[str] = []
        for ref in request.accounts:
            if ref.role not in self.roles:
                continue
            api = self.api_for(ref.account_id)
            resources += self._collect_owner(api, ref, request.until, accounts, posts, metrics, caveats)
        return CollectionBatch(
            provider=self.name,
            accounts=tuple(accounts),
            posts=tuple(posts),
            metrics=tuple(metrics),
            cost_usd=round(len(resources) * OWNED_READ_USD, 6),
            # Billed once per resource per UTC day by the collector, as X deduplicates Owned Reads.
            billable=tuple(BillableRead(resource=r, unit_cost_usd=OWNED_READ_USD) for r in resources),
            caveats=tuple(caveats),
        )

    def _collect_owner(
        self,
        api: XApi,
        ref: AccountRef,
        until: datetime,
        accounts: list[FetchedAccount],
        posts: list[FetchedPost],
        metrics: list[FetchedMetrics],
        caveats: list[str],
    ) -> list[str]:
        """Collect one owner account; returns the billable resources returned (``post:<id>`` …)."""
        try:
            me_page = api.get("/2/users/me", {"user.fields": "public_metrics"}, key="users_me")
        except RateLimited as exc:
            caveats.append(f"Skipped {ref.handle}: {exc}. Collection carries to the next run.")
            return []
        me = me_page.body["data"]
        own_id = str(me["id"])
        resources = [f"user:{own_id}"]
        if ref.external_id is not None and ref.external_id != own_id:
            raise ReauthRequired(
                f"The stored X credentials are for @{me.get('username')} ({own_id}), not {ref.handle}."
            )
        fetched_at = self.clock()

        n_timeline, fallbacks = self._timeline(api, ref, own_id, until, fetched_at, posts, metrics, caveats)
        resources += n_timeline
        if fallbacks:
            caveats.append(
                f"{fallbacks} post(s) had no non-public metrics; impressions use the public impression_count."
            )

        new_followers, n_followers = self._new_followers(api, own_id, ref.known_follower_ids, caveats)
        resources += n_followers
        accounts.append(
            FetchedAccount(
                account_id=ref.account_id,
                external_id=own_id,
                handle=ref.handle,
                followers=int(me["public_metrics"]["followers_count"]),
                fetched_at=fetched_at,
                new_follower_ids=new_followers,
            )
        )
        return resources

    def _timeline(
        self,
        api: XApi,
        ref: AccountRef,
        own_id: str,
        until: datetime,
        fetched_at: datetime,
        posts: list[FetchedPost],
        metrics: list[FetchedMetrics],
        caveats: list[str],
    ) -> tuple[list[str], int]:
        params: dict[str, Any] = {
            "max_results": TIMELINE_PAGE,
            "start_time": _iso(until - TIMELINE_WINDOW),
            "tweet.fields": TWEET_FIELDS,
            "expansions": "attachments.media_keys",
            "media.fields": "type",
        }
        returned: list[str] = []
        fallbacks = 0
        while True:
            try:
                page = api.get(f"/2/users/{own_id}/tweets", params, key="user_tweets")
            except RateLimited as exc:
                caveats.append(f"Timeline paging stopped early: {exc}. The rest carries to the next run.")
                break
            data = page.body.get("data") or []
            returned += [f"post:{t['id']}" for t in data]
            media_types = {
                m["media_key"]: m.get("type", "")
                for m in (page.body.get("includes") or {}).get("media") or ()
            }
            if page.body.get("errors"):
                caveats.append(f"The timeline returned {len(page.body['errors'])} partial error(s).")
            for tweet in data:
                post = to_post(tweet, own_id, ref.account_id, media_types)
                if post.created_at > until:
                    continue  # after this run's cutoff; the next run takes it
                if "non_public_metrics" not in tweet:
                    fallbacks += 1
                posts.append(post)
                metrics.append(to_metrics(tweet, fetched_at))
            next_token = (page.body.get("meta") or {}).get("next_token")
            if not next_token:
                break
            if page.rate.remaining == 0:
                caveats.append(
                    f"Timeline paging stopped: rate limit exhausted ({page.rate.describe()}). "
                    "The rest carries to the next run."
                )
                break
            params["pagination_token"] = next_token
        return returned, fallbacks

    def _new_followers(
        self, api: XApi, own_id: str, known: tuple[str, ...], caveats: list[str]
    ) -> tuple[tuple[str, ...] | None, list[str]]:
        """Page the newest-first follower list until a known follower; returns (new ids, records read)."""
        known_set = set(known)
        baseline = not known_set
        params: dict[str, Any] = {"max_results": FOLLOWER_BASELINE if baseline else FOLLOWER_PAGE}
        new: list[str] = []
        returned: list[str] = []
        for _ in range(MAX_FOLLOWER_PAGES):
            try:
                page = api.get(f"/2/users/{own_id}/followers", params, key="followers")
            except RateLimited as exc:
                caveats.append(f"New followers not counted this run: {exc}.")
                return None, returned
            data = page.body.get("data") or []
            returned += [f"follower:{u['id']}" for u in data]
            for user in data:
                if str(user["id"]) in known_set:
                    return tuple(new), returned
                new.append(str(user["id"]))
            next_token = (page.body.get("meta") or {}).get("next_token")
            if baseline or not next_token:
                return tuple(new), returned
            if page.rate.remaining == 0:
                caveats.append(
                    f"New followers not counted this run: rate limit exhausted ({page.rate.describe()})."
                )
                return None, returned
            params["pagination_token"] = next_token
        caveats.append(
            f"More than {MAX_FOLLOWER_PAGES * FOLLOWER_PAGE} followers without reaching a known one; "
            "new followers not counted this run."
        )
        return None, returned
