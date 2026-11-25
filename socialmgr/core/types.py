"""Provider-agnostic domain types exchanged across the extension interfaces."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

PostType = Literal["original", "quote", "reply", "repost", "self_reply"]
AccountRole = Literal["owner", "peer", "competitor"]
ForecastKind = Literal["live", "as_of", "treatment_blind", "prior_only_uncalibrated"]


class Capability(StrEnum):
    """What a data provider can supply. The collector plans each run from these."""

    OWNER_METRICS = "owner_metrics"  # non-public metrics for the authenticated owner
    PUBLIC_METRICS = "public_metrics"  # public counts for any account
    OWNER_ANALYTICS = "owner_analytics"  # hourly/daily analytics buckets
    FOLLOWER_LISTS = "follower_lists"  # newest-first follower list
    SEARCH = "search"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class AccountRef(Frozen):
    """An account the collector is asked about."""

    account_id: int
    platform: str
    handle: str
    role: AccountRole
    external_id: str | None = None


class FetchedAccount(Frozen):
    account_id: int
    external_id: str
    handle: str
    followers: int
    fetched_at: datetime


class FetchedPost(Frozen):
    account_id: int
    external_id: str
    created_at: datetime
    type: PostType
    text: str
    conversation_id: str | None = None
    in_reply_to_user_id: str | None = None
    has_url: bool = False
    media_kind: str | None = None
    format_hint: str | None = None
    topic_hint: str | None = None


class FetchedMetrics(Frozen):
    post_external_id: str
    fetched_at: datetime
    impressions: int | None
    impressions_source: str
    likes: int | None
    reposts: int | None
    quotes: int | None
    replies: int | None
    bookmarks: int | None
    profile_clicks: int | None = None
    link_clicks: int | None = None


class CollectionRequest(Frozen):
    accounts: tuple[AccountRef, ...]
    since: datetime | None
    until: datetime


class CollectionBatch(Frozen):
    provider: str
    accounts: tuple[FetchedAccount, ...] = ()
    posts: tuple[FetchedPost, ...] = ()
    metrics: tuple[FetchedMetrics, ...] = ()
    cost_usd: float = 0.0


class Distribution(Frozen):
    family: Literal["normal", "half_normal", "lognormal", "beta", "gamma"]
    params: dict[str, float]


class PriorSpec(Frozen):
    """Named priors a model may read. Models ignore names they don't use."""

    source: str
    version: str
    params: dict[str, Distribution]


class PriorContext(Frozen):
    platform: str
    followers: int
    owner_matured_posts: int


class TrainingExample(Frozen):
    """A matured outcome visible at the training cutoff."""

    post_external_id: str
    account_role: AccountRole
    created_at: datetime
    type: PostType
    followers_at_post: int
    outcome_i48: float


class ForecastSubject(Frozen):
    """What a forecast is about: a planned or published post, described by pre-post information only."""

    subject_id: str
    created_at: datetime
    type: PostType
    followers_at_post: int
    has_url: bool = False
    format: str | None = None
    topic: str | None = None


@dataclass(frozen=True)
class PredictiveDraws:
    """Joint predictive draws for one subject. Scoring is model-agnostic because it only reads these."""

    metric: str
    horizon_h: float
    samples: NDArray[np.float64]
    meta: dict[str, str] = field(default_factory=dict)

    def quantile(self, q: float) -> float:
        return float(np.quantile(self.samples, q))
