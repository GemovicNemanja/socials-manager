"""Deterministic synthetic accounts.

Every post and count is a pure function of (seed, handle, day), so repeated runs over the same
window return the same data, and a later run sees the same posts at an older age.
"""

import hashlib
import math
from datetime import UTC, datetime, timedelta

import numpy as np

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
from socialmgr.modelling.maturity import PRIOR, Curve

DEFAULT_START = datetime(2026, 10, 13, tzinfo=UTC)
POST_HOURS = (7, 11, 16)  # UTC
TYPES: tuple[PostType, ...] = ("original", "quote", "reply", "self_reply")
TYPE_P = (0.7, 0.1, 0.15, 0.05)
TYPE_REACH = {"original": 1.0, "quote": 0.9, "reply": 0.15, "self_reply": 0.1, "repost": 0.1}
FORMATS = ("Text", "Image", "Thread", "Narrated video", "Video", "Text + link", "Poll")
TOPICS = ("Build log", "Demo", "Method", "Explainer", "Progress", "Commentary")
SUBJECTS = (
    "the forecast band",
    "posting cadence",
    "a daily run",
    "the maturity curve",
    "frozen forecasts",
    "experiment slots",
    "the drift alarm",
    "follower funnels",
)
OPENERS = (
    "How I built",
    "Notes on",
    "What I learned from",
    "A quick look at",
    "Week in review:",
    "Why I changed",
)
MAX_AGE = timedelta(days=30)


def _rng(*parts: object) -> np.random.Generator:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _days(since: datetime, until: datetime) -> list[datetime]:
    day = since.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    while day <= until:
        out.append(day)
        day += timedelta(days=1)
    return out


class _SyntheticAccount:
    def __init__(self, seed: int, ref: AccountRef, start: datetime, growth_per_day: float) -> None:
        self.seed = seed
        self.ref = ref
        self.start = start
        self.growth = growth_per_day
        self.external_id = ref.external_id or str(
            int(hashlib.sha256(ref.handle.encode()).hexdigest()[:12], 16)
        )

    def followers_at(self, t: datetime) -> int:
        days = max((t - self.start).total_seconds() / 86400, 0.0)
        return round(self.growth * days)

    def posts_on(self, day: datetime) -> list[tuple[FetchedPost, float]]:
        if day < self.start.replace(hour=0, minute=0, second=0, microsecond=0):
            return []
        rng = _rng(self.seed, self.ref.handle, day.date().isoformat())
        out = []
        for i, hour in enumerate(POST_HOURS):
            created = day + timedelta(hours=hour, minutes=int(rng.integers(-20, 21)))
            if created < self.start:
                continue
            ptype: PostType = TYPES[int(rng.choice(len(TYPES), p=TYPE_P))]
            fmt = FORMATS[int(rng.integers(len(FORMATS)))]
            topic = TOPICS[int(rng.integers(len(TOPICS)))]
            text = f"{OPENERS[int(rng.integers(len(OPENERS)))]} {SUBJECTS[int(rng.integers(len(SUBJECTS)))]}"
            followers = self.followers_at(created)
            mean = (150 + 8 * followers**0.75) * TYPE_REACH[ptype]
            i48 = float(rng.lognormal(math.log(mean), 0.8))
            post = FetchedPost(
                account_id=self.ref.account_id,
                external_id=f"{self.external_id}{day.strftime('%y%m%d')}{i}",
                created_at=created,
                type=ptype,
                text=text,
                has_url=fmt == "Text + link",
                media_kind={"Image": "photo", "Video": "video", "Narrated video": "video"}.get(fmt),
                format_hint=fmt,
                topic_hint=topic,
            )
            out.append((post, i48))
        return out


class _FakeProvider:
    name: str
    capabilities: frozenset[Capability]
    roles: frozenset[str]
    impressions_source: str
    owner_metrics: bool
    cost_per_post_read: float
    cost_per_user_read: float

    def __init__(
        self,
        seed: int = 7,
        start: datetime = DEFAULT_START,
        growth_per_day: float = 9.6,
        curve: Curve = PRIOR,
    ) -> None:
        self.seed = seed
        self.start = start
        self.growth = growth_per_day
        self.curve = curve  # the true maturity curve the synthetic trajectories follow

    def collect(self, request: CollectionRequest) -> CollectionBatch:
        accounts, posts, metrics = [], [], []
        billable: list[BillableRead] = []
        cost = 0.0
        for ref in request.accounts:
            if ref.role not in self.roles:
                continue
            acct = _SyntheticAccount(self.seed, ref, self.start, self.growth)
            accounts.append(
                FetchedAccount(
                    account_id=ref.account_id,
                    external_id=acct.external_id,
                    handle=ref.handle,
                    followers=acct.followers_at(request.until),
                    fetched_at=request.until,
                )
            )
            cost += self.cost_per_user_read
            billable.append(
                BillableRead(resource=f"user:{acct.external_id}", unit_cost_usd=self.cost_per_user_read)
            )
            window_start = request.until - MAX_AGE
            for day in _days(max(window_start, self.start), request.until):
                for post, i48 in acct.posts_on(day):
                    if not window_start < post.created_at <= request.until:
                        continue
                    if request.since is None or post.created_at > request.since:
                        posts.append(post)
                    metrics.append(self._metrics(post, i48, request.until))
                    cost += self.cost_per_post_read
                    billable.append(
                        BillableRead(
                            resource=f"post:{post.external_id}", unit_cost_usd=self.cost_per_post_read
                        )
                    )
        return CollectionBatch(
            provider=self.name,
            accounts=tuple(accounts),
            posts=tuple(posts),
            metrics=tuple(metrics),
            cost_usd=round(cost, 6),
            billable=tuple(billable),
        )

    def _metrics(self, post: FetchedPost, i48: float, at: datetime) -> FetchedMetrics:
        age_h = (at - post.created_at).total_seconds() / 3600
        impressions = round(i48 * self.curve.g(age_h))
        rng = _rng(self.seed, post.external_id, "rates")
        like_rate, repost_rate, reply_rate, quote_rate, bm_rate, pc_rate = (
            rng.uniform(0.015, 0.035),
            rng.uniform(0.001, 0.005),
            rng.uniform(0.002, 0.008),
            rng.uniform(0.0003, 0.0015),
            rng.uniform(0.002, 0.008),
            rng.uniform(0.01, 0.03),
        )
        return FetchedMetrics(
            post_external_id=post.external_id,
            fetched_at=at,
            impressions=impressions,
            impressions_source=self.impressions_source,
            likes=round(impressions * like_rate),
            reposts=round(impressions * repost_rate),
            quotes=round(impressions * quote_rate),
            replies=round(impressions * reply_rate),
            bookmarks=round(impressions * bm_rate),
            profile_clicks=round(impressions * pc_rate) if self.owner_metrics else None,
            link_clicks=round(impressions * 0.01) if self.owner_metrics and post.has_url else None,
        )


class FakeOwnerProvider(_FakeProvider):
    """Stands in for the official X API with the owner's credentials (owner + non-public metrics)."""

    name = "fake_owner"
    capabilities = frozenset({Capability.OWNER_METRICS, Capability.PUBLIC_METRICS})
    roles = frozenset({"owner"})
    impressions_source = "impression_count"
    owner_metrics = True
    cost_per_post_read = 0.001  # simulated Owned Read price
    cost_per_user_read = 0.01


class FakePublicProvider(_FakeProvider):
    """Stands in for a public-data provider (a peer source): public counts only, viewCount impressions."""

    name = "fake_public"
    capabilities = frozenset({Capability.PUBLIC_METRICS, Capability.SEARCH})
    roles = frozenset({"peer", "competitor"})
    impressions_source = "viewCount"
    owner_metrics = False
    cost_per_post_read = 0.00015
    cost_per_user_read = 0.00001
