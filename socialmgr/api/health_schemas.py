"""Response shapes for M3: the Forecasts screen (model health), followers and goals.

The web app and the MCP tools read the same shapes. Every expectation that has a past
counterpart also carries how that past result landed (plan §6, "Always show how the past did").
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from socialmgr.services.read_models import Envelope

Outcome = Literal["above", "as_expected", "below"]
GoalStatusName = Literal["working", "near", "not_working", "no_goal"]
Stage = Literal["prior_only", "owner"]


# ---------------------------------------------------------------- Forecasts screen


class GateOut(BaseModel):
    """One release gate (MODEL §5). ``passed`` is None when it couldn't be evaluated."""

    key: str
    label: str
    passed: bool | None
    value: float | None
    threshold: str
    detail: str


class ReviewOut(BaseModel):
    at: datetime
    run_id: int
    decision: Literal["promoted", "held", "not_eligible", "kept"]
    reason: str
    candidate: str
    champion_before: str
    champion_after: str
    owner_n: int
    gates: list[GateOut]


class HitRateTile(BaseModel):
    """Share of matured posts whose outcome fell inside the frozen central interval."""

    level: float  # 0.5 or 0.8
    value: float | None
    n: int
    band_lo: float | None  # exact binomial 95% band for n posts
    band_hi: float | None
    in_band: bool | None


class SkillTile(BaseModel):
    """Log-CRPS skill against the trailing-median baseline, week-block bootstrap 90% interval."""

    value: float | None
    ci90_lo: float | None
    ci90_hi: float | None
    passed: bool | None
    n: int


class CalibrationTile(BaseModel):
    berkowitz_p: float | None
    pit_chi2_p: float | None
    passed: bool | None
    n: int


class DriftTile(BaseModel):
    """D1 location CUSUM on 48h impressions."""

    armed: bool
    stat: float | None
    threshold: float | None
    state: Literal["off", "quiet", "alarm"]
    last_alarm_at: datetime | None
    reason: str


class PitHistogram(BaseModel):
    """PIT of 48h impressions in 10 bins, last ``n`` originals and quotes. Flat means calibrated."""

    counts: list[int]
    n: int
    band_lo: float | None  # per-bin count band (90%) if calibrated
    band_hi: float | None


class CoverageRow(BaseModel):
    level: float  # 0.5, 0.8, 0.95
    value: float | None
    n: int


class Elasticity(BaseModel):
    q50: float
    q10: float
    q90: float


class ModelCardOut(BaseModel):
    champion: str  # registry name
    version: str | None
    stage: Stage
    outcome_model: str
    prior: str
    follower_elasticity: Elasticity | None
    post_features: list[str]
    maturity_curve: str
    challenger: str
    promotion_rule: str
    last_promotion: datetime | None
    refit: str


class ChangeRowOut(BaseModel):
    """A dated change that can shift performance: ranker changes, model releases, goals."""

    at: datetime
    source: Literal["Model", "X ranker repo", "Account", "Goal", "Monitor"]
    text: str
    action: str


class ChartPoint(BaseModel):
    at: datetime
    value: float


class MonitorOut(BaseModel):
    chart: str  # d1_location | d1_scale | d1_followers | d2 | d3
    label: str
    armed: bool
    stat: float | None
    threshold: float | None
    state: Literal["off", "quiet", "alarm", "proposal"]
    detail: str
    series: list[ChartPoint]


class AlarmOut(BaseModel):
    id: int
    chart: str
    label: str
    raised_at: datetime
    direction: str | None
    stat: float | None
    threshold: float | None
    attribution: str
    acknowledged_at: datetime | None
    note: str | None


class ModelHealthOut(Envelope):
    summary: str
    stage: Stage
    owner_n: int
    owner_terms_at: int  # matured owner posts needed for owner-level terms (20)
    hit_80: HitRateTile
    hit_50: HitRateTile
    skill: SkillTile
    calibration: CalibrationTile
    drift: DriftTile
    pit: PitHistogram
    coverage: list[CoverageRow]
    width_ratio: float | None  # 80% width vs the baseline's
    next_check_in_posts: int | None
    card: ModelCardOut
    last_review: ReviewOut | None
    reviews: list[ReviewOut]
    changes: list[ChangeRowOut]
    monitors: list[MonitorOut]
    alarms: list[AlarmOut]


class AckIn(BaseModel):
    note: str | None = None


class AckOut(BaseModel):
    acknowledged: bool
    summary: str
    alarm: AlarmOut | None


# ---------------------------------------------------------------- followers and goals


class FollowerRange(BaseModel):
    q10: float
    q50: float
    q90: float


class ExpectedVsActual(BaseModel):
    """A past expectation and how the actual landed against it (MODEL §4.1 thresholds)."""

    expected: FollowerRange
    actual: float
    percentile: float
    outcome: Outcome
    made_at: datetime


class CountTile(BaseModel):
    value: float | None
    per_day: float | None
    expected: ExpectedVsActual | None
    note: str | None


class FollowerTiles(BaseModel):
    followers: CountTile  # value = followers now; expected = the 7-day forecast made 7 days ago
    net_7d: int | None
    new_follows_7d: CountTile
    unfollows_7d: CountTile
    profile_visits_7d: CountTile
    visit_to_follow_7d: CountTile  # value is a share 0..1
    replies_7d: CountTile


class FollowerBandPoint(BaseModel):
    at: datetime
    q10: float
    q50: float
    q90: float


class FollowerForecastOut(BaseModel):
    id: int
    made_at: datetime
    kind: Literal["prior_only", "calibrated"]
    model_version: str
    start_followers: int
    band: list[FollowerBandPoint]  # daily, from made_at to the longest horizon
    horizons: dict[str, FollowerRange]  # "7", "30", "90"


class FollowerScoreOut(BaseModel):
    """A frozen follower forecast whose horizon has passed, against the actual count."""

    forecast_id: int
    made_at: datetime
    horizon_days: int
    target_at: datetime
    expected: FollowerRange
    actual: int
    percentile: float
    outcome: Outcome


class FunnelStep(BaseModel):
    key: Literal["impressions", "profile_visits", "new_follows", "unfollows", "net"]
    label: str
    value: float | None
    note: str


class ScenarioOut(BaseModel):
    label: str
    posts_per_day: float
    q50_30d: float
    p_goal: float | None


class GoalOut(BaseModel):
    id: int
    scope: Literal["posts", "followers"]
    metric: str
    target: float
    target_at: datetime | None
    created_at: datetime
    created_by: str
    superseded_at: datetime | None


class GoalStatusOut(BaseModel):
    goal: GoalOut
    status: GoalStatusName
    reason: str | None
    p_goal: float | None  # P(metric at target date >= target)
    p_hi: float | None
    p_lo: float | None
    since: datetime | None
    previous_status: str | None
    median: float | None
    q10: float | None
    q90: float | None
    summary: str


class FollowersOut(Envelope):
    summary: str
    tiles: FollowerTiles
    actual: list[ChartPoint]  # followers per run
    forecast: FollowerForecastOut | None
    annotations: list[ChangeRowOut]
    funnel: list[FunnelStep]
    goal: GoalStatusOut | None
    past: list[FollowerScoreOut]
    scenarios: list[ScenarioOut]
    model_kind: Literal["prior_only", "calibrated", "none"]
    intervals_n: int


class GoalIn(BaseModel):
    scope: Literal["posts", "followers"]
    target: float
    target_at: datetime | None = None
    note: str | None = None


class GoalPreviewOut(BaseModel):
    """Where the member would stand today against a goal they haven't saved yet."""

    status: GoalStatusName
    reason: str | None
    p_goal: float | None
    median: float | None
    q10: float | None
    q90: float | None
    paths: int
    summary: str


class GoalsOut(Envelope):
    summary: str
    current: list[GoalStatusOut]
    history: list[GoalOut]


class GoalSetOut(BaseModel):
    saved: bool
    summary: str
    goal: GoalOut | None
