"""The public journal's data: one JSON snapshot the static site renders (plan §9, M4).

Only what is meant to be public goes in here: follower counts, scored-post totals, forecast hit
rates, experiment results and the weekly entries the owner published. No tokens, no per-post
non-public metrics beyond impressions and likes per 1k, and no drafts unless ``preview`` is set
(the local preview shows drafts; a published site never does).
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Outcome = Literal["above", "as_expected", "below", "provisional"]
ExperimentResult = Literal["running", "effect_found", "no_effect", "stopped", "scheduled"]


class PublicSite(BaseModel):
    handle: str  # "@name"
    x_url: str  # https://x.com/name
    tagline: str  # "growing in public"
    headline: str  # the home page title
    intro: str  # the paragraph under it
    started_on: date | None  # the first run: "from N on <date>"
    updated_at: datetime | None  # the last run that refreshed the numbers
    week_number: int  # weeks since started_on (week 0 is the first)
    site_url: str | None  # where the site is published, for share links


class PublicStats(BaseModel):
    followers_now: int | None
    followers_start: int | None
    posts_scored: int  # owner posts scored against their frozen forecast
    hit_80: float | None  # share of scored posts inside their 80% range
    experiments_total: int
    experiments_with_effect: int


class SeriesPoint(BaseModel):
    at: datetime
    value: float


class Marker(BaseModel):
    at: datetime
    label: str  # "EXP-012 started"


class MethodFact(BaseModel):
    label: str
    value: str


class ArmPoint(BaseModel):
    """One matured post in an experiment: its outcome as a share of its own frozen forecast."""

    arm: str  # arm label
    ratio: float  # outcome / frozen treatment-blind forecast median


class ArmSummary(BaseModel):
    label: str
    n: int
    typical_ratio: float | None  # median ratio of the arm's posts


class PublicExperiment(BaseModel):
    key: str  # "E03"
    slug: str  # url-safe key, "e03"
    name: str
    question: str  # the page title: the hypothesis as a question
    hypothesis: str
    answer: str | None  # one or two sentences: what happened (owner-written, else templated)
    design_label: str  # "Per-post, randomized" / "Switchback, 4-day blocks"
    metric_label: str
    result: ExperimentResult
    posts_matured: int
    posts_planned: int
    progress_label: str  # "41 / 84" or "block 3 / 8"
    effect_ratio: float | None  # treated vs control, each post against its frozen forecast
    effect_ci80: tuple[float, float] | None
    p_better: float | None  # posterior P(effect > 1)
    confirmed: bool  # an e-LOND rejection: the only route to "confirmed"
    randomization_p: float | None
    started_at: datetime | None
    concluded_at: datetime | None
    registered_at: datetime | None  # when the plan was frozen, before any post was assigned
    arms: list[ArmSummary]  # control first
    points: list[ArmPoint]  # for the dot chart
    why_markdown: str | None  # "Why I tested it" (owner-written, else the mechanism)
    changed_markdown: str | None  # "What I changed" (owner-written; absent until concluded)
    setup: list[MethodFact]  # "How it was set up, written before the first post"
    note_slug: str | None  # the weekly entry that discusses it
    related_keys: list[str]


class PublicPostRow(BaseModel):
    """A post in a weekly entry's table, against its frozen forecast."""

    text: str  # snippet
    url: str | None  # link to the post on X
    format: str | None
    topic: str | None
    i2h: float | None  # impressions at the capture closest to 2h, when one exists
    i48: float | None
    likes_per_1k: float | None
    q10: float | None
    q50: float | None
    q90: float | None
    outcome: Outcome


class EntryStats(BaseModel):
    followers_delta: int | None
    followers_expected: tuple[float, float] | None  # the frozen follower forecast's 80% range
    impressions: float | None
    impressions_change: float | None  # vs the previous week, as a ratio - 1
    posts_scored: int
    posts_in_80: int
    hit_80: float | None


class PublicEntry(BaseModel):
    slug: str  # "week-0"
    week_number: int
    period_start: date
    period_end: date
    published_at: datetime | None  # None only in a preview (a draft)
    draft: bool
    title: str
    summary: str  # the first paragraph, for lists
    body_markdown: str
    stats: EntryStats
    posts: list[PublicPostRow]  # top posts of the week by 48h impressions (at most 5)
    posts_total: int  # posts in the week
    experiment_keys: list[str]


class CoverageRow(BaseModel):
    label: str  # "Inside the 80% range (target 80%)"
    target: float
    value: float | None


class MethodSection(BaseModel):
    number: int
    title: str  # "What we measure"
    body: str  # one paragraph
    facts: list[MethodFact]
    coverage: list[CoverageRow]  # meters (only the "are the forecasts honest" section has them)


class PublicMethod(BaseModel):
    headline: str
    intro: str
    sections: list[MethodSection]
    model_stage: Literal["prior_only", "owner"]
    n_scored: int


class JournalSnapshot(BaseModel):
    schema_version: Literal[1] = 1
    preview: bool  # true in the local preview (drafts included and marked)
    site: PublicSite
    stats: PublicStats
    followers: list[SeriesPoint]
    markers: list[Marker]
    experiments: list[PublicExperiment]
    entries: list[PublicEntry]  # newest first
    method: PublicMethod
