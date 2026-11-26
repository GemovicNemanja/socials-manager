"""Goal status for a scope (MODEL.md §4.2), with hysteresis.

With ε = log 1.1 and θ the scope's log metric:
    p_hi = P(θ > log G + ε),   p_lo = P(θ < log G - ε)
- Working: p_hi ≥ 0.80. Not working: p_lo ≥ 0.80. Otherwise Near threshold with a reason:
  ``insufficient_owner_data`` (fewer than 5 posts in scope, or a prior-only follower fit),
  ``uncertain`` (80% interval of θ wider than 4ε), else ``close``.
- Hysteresis: leave Working only when p_hi < 0.70 (to Near) or p_lo ≥ 0.85 (to Not working);
  Not working is symmetric; from Near the 0.80 entry thresholds apply. A changed goal or scope
  starts afresh (goals are frozen per period, and every change is logged by the caller).

Scopes in v1:
- **Followers**: θ = log F_T from the follower forecast (``follower_goal_status``). The
  probabilities come from the forecast's variance-reduced estimator.
- **Posts** (the last 21 originals, metric I48): θ_S = log of the posterior median I48 for the
  scope's average post at current conditions (``posts_goal_status``). The reach model supplies
  predictive draws of that log median; a scope effect b_S ~ N(0, 0.3²) is updated by the scope's
  observed outcomes. **Approximation:** each scope post's log residual r_i = log(1 + y_i) -
  log(1 + median_i) is treated as an independent N(b_S, s_i²) observation, with s_i the post's
  predictive log-scale sd. That ignores the shared parameter uncertainty already in s_i (so
  b_S's update is a little conservative) and any overlap with owner-level terms the reach model
  has already learnt from the same posts. The update is conjugate; p_hi and p_lo integrate b_S
  analytically over each draw, so they are deterministic given the draws.

No default goals in v1 (plan §6): without a goal the status is ``no_goal`` ("no goal set").
Status objects carry ``since`` and ``previous_status`` so the caller can keep the history.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy import optimize, special

from socialmgr.modelling.followers.forecast import FollowerForecast, FollowerGoal

EPSILON = math.log(1.1)
ENTER = 0.80
LEAVE = 0.70
FLIP = 0.85
UNCERTAIN_WIDTH = 4.0 * EPSILON
MIN_SCOPE_POSTS = 5
SCOPE_SIZE = 21
SCOPE_EFFECT_SD = 0.3

FOLLOWER_SCOPE = "followers"
POSTS_SCOPE = "last_21_originals"

Status = Literal["working", "near_threshold", "not_working", "no_goal"]
Reason = Literal["close", "uncertain", "insufficient_owner_data", "no_goal_set"]

STATUS_LABELS: dict[str, str] = {
    "working": "Working",
    "near_threshold": "Near threshold",
    "not_working": "Not working",
    "no_goal": "No goal set",
}


@dataclass(frozen=True)
class GoalStatus:
    scope: str
    status: Status
    reason: Reason | None
    goal: float | None
    p_hi: float | None
    p_lo: float | None
    theta_median: float | None  # on the log scale
    interval80: tuple[float, float] | None  # 80% interval of θ (log scale)
    as_of: datetime
    since: datetime  # when the current status began
    previous_status: Status | None  # the status before the current spell, for history
    horizon_days: int | None = None  # follower goals
    detail: Mapping[str, float | int | str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "status": self.status,
            "label": self.label,
            "reason": self.reason,
            "goal": self.goal,
            "p_hi": self.p_hi,
            "p_lo": self.p_lo,
            "theta_median": self.theta_median,
            "interval80": None if self.interval80 is None else list(self.interval80),
            "as_of": self.as_of.isoformat(),
            "since": self.since.isoformat(),
            "previous_status": self.previous_status,
            "horizon_days": self.horizon_days,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "GoalStatus":
        iv = d.get("interval80")
        return cls(
            scope=d["scope"],
            status=d["status"],
            reason=d.get("reason"),
            goal=None if d.get("goal") is None else float(d["goal"]),
            p_hi=None if d.get("p_hi") is None else float(d["p_hi"]),
            p_lo=None if d.get("p_lo") is None else float(d["p_lo"]),
            theta_median=None if d.get("theta_median") is None else float(d["theta_median"]),
            interval80=None if iv is None else (float(iv[0]), float(iv[1])),
            as_of=datetime.fromisoformat(d["as_of"]),
            since=datetime.fromisoformat(d["since"]),
            previous_status=d.get("previous_status"),
            horizon_days=None if d.get("horizon_days") is None else int(d["horizon_days"]),
            detail=dict(d.get("detail", {})),
        )


def classify(
    p_hi: float,
    p_lo: float,
    width80: float,
    *,
    insufficient: bool = False,
    previous: Status | None = None,
) -> tuple[Status, Reason | None]:
    """MODEL §4.2 status with hysteresis against the previous status (None: no history)."""
    if insufficient:
        return "near_threshold", "insufficient_owner_data"
    status: Status
    if previous == "working":
        status = "not_working" if p_lo >= FLIP else ("near_threshold" if p_hi < LEAVE else "working")
    elif previous == "not_working":
        status = "working" if p_hi >= FLIP else ("near_threshold" if p_lo < LEAVE else "not_working")
    elif p_hi >= ENTER:
        status = "working"
    elif p_lo >= ENTER:
        status = "not_working"
    else:
        status = "near_threshold"
    if status != "near_threshold":
        return status, None
    return status, ("uncertain" if width80 > UNCERTAIN_WIDTH else "close")


def _carry(
    scope: str, goal: float, status: Status, as_of: datetime, previous: GoalStatus | None, horizon: int | None
) -> tuple[Status | None, datetime, Status | None]:
    """(previous status for hysteresis, since, previous_status for history)."""
    usable = (
        previous is not None
        and previous.scope == scope
        and previous.goal == goal
        and previous.horizon_days == horizon
        and previous.status != "no_goal"
        and previous.as_of <= as_of
    )
    if not usable or previous is None:
        return None, as_of, (previous.status if previous is not None else None)
    if previous.status == status:
        return previous.status, previous.since, previous.previous_status
    return previous.status, as_of, previous.status


def _status(
    scope: str,
    goal: float,
    p_hi: float,
    p_lo: float,
    theta_median: float,
    interval80: tuple[float, float],
    *,
    as_of: datetime,
    previous: GoalStatus | None,
    insufficient: bool,
    horizon_days: int | None,
    detail: Mapping[str, float | int | str],
) -> GoalStatus:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    hyst, _, _ = _carry(scope, goal, "no_goal", as_of, previous, horizon_days)
    status, reason = classify(
        p_hi, p_lo, interval80[1] - interval80[0], insufficient=insufficient, previous=hyst
    )
    _, since, prev_status = _carry(scope, goal, status, as_of, previous, horizon_days)
    return GoalStatus(
        scope=scope,
        status=status,
        reason=reason,
        goal=goal,
        p_hi=p_hi,
        p_lo=p_lo,
        theta_median=theta_median,
        interval80=interval80,
        as_of=as_of,
        since=since,
        previous_status=prev_status,
        horizon_days=horizon_days,
        detail=dict(detail),
    )


def no_goal_status(scope: str, as_of: datetime, previous: GoalStatus | None = None) -> GoalStatus:
    """'No goal set' (plan §6: no default goals in v1)."""
    keep = previous is not None and previous.scope == scope and previous.status == "no_goal"
    return GoalStatus(
        scope=scope,
        status="no_goal",
        reason="no_goal_set",
        goal=None,
        p_hi=None,
        p_lo=None,
        theta_median=None,
        interval80=None,
        as_of=as_of,
        since=previous.since if keep and previous is not None else as_of,
        previous_status=(previous.previous_status if keep else previous.status)
        if previous is not None
        else None,
    )


# --- follower goals --------------------------------------------------------------------------------


def follower_goal_status(
    forecast: FollowerForecast,
    goal: FollowerGoal,
    *,
    as_of: datetime | None = None,
    previous: GoalStatus | None = None,
    insufficient: bool | None = None,
) -> GoalStatus:
    """θ = log F_T at the goal's horizon. ``insufficient`` defaults to a prior-only follower fit."""
    day = goal.horizon_days
    hi_cut = goal.target * math.exp(EPSILON)
    lo_cut = goal.target * math.exp(-EPSILON)
    p_hi = forecast.prob_gt(day, hi_cut)
    p_lo = forecast.prob_lt(day, lo_cut)
    draws = forecast.draws_at(day).astype(np.float64)
    q10, q50, q90 = np.quantile(np.log(np.maximum(draws, 1.0)), [0.1, 0.5, 0.9])
    flag = forecast.fit_label == "prior_only" if insufficient is None else insufficient
    return _status(
        FOLLOWER_SCOPE,
        float(goal.target),
        p_hi,
        p_lo,
        float(q50),
        (float(q10), float(q90)),
        as_of=as_of or forecast.as_of,
        previous=previous,
        insufficient=flag,
        horizon_days=day,
        detail={"fit_label": forecast.fit_label, "forecast_hash": forecast.manifest_hash},
    )


# --- posts goals ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopePost:
    """One post in the scope: its observed I48 and the frozen forecast's log-scale median and sd."""

    post_id: str
    observed_i48: float
    pred_log_median: float  # log(1 + median I48) under the post's frozen forecast
    pred_log_sd: float  # sd of log(1 + I48) under that forecast

    @classmethod
    def from_draws(
        cls, post_id: str, observed_i48: float, draws: Sequence[float] | NDArray[np.float64]
    ) -> "ScopePost":
        x = np.log1p(np.maximum(np.asarray(draws, dtype=np.float64), 0.0))
        return cls(post_id, float(observed_i48), float(np.median(x)), max(float(np.std(x)), 1e-3))

    @property
    def log_residual(self) -> float:
        return math.log1p(max(self.observed_i48, 0.0)) - self.pred_log_median


@dataclass(frozen=True)
class ScopeEffect:
    mean: float
    sd: float
    n_posts: int


def scope_effect(posts: Sequence[ScopePost], prior_sd: float = SCOPE_EFFECT_SD) -> ScopeEffect:
    """Conjugate normal update of b_S ~ N(0, prior_sd²) by the posts' log residuals."""
    precision = 1.0 / prior_sd**2
    weighted = 0.0
    for p in posts:
        w = 1.0 / max(p.pred_log_sd, 1e-3) ** 2
        precision += w
        weighted += w * p.log_residual
    return ScopeEffect(weighted / precision, math.sqrt(1.0 / precision), len(posts))


def _mixture_quantile(base: NDArray[np.float64], sd: float, q: float) -> float:
    def cdf(x: float) -> float:
        return float(np.mean(special.ndtr((x - base) / sd))) - q

    lo, hi = float(base.min()) - 10.0 * sd, float(base.max()) + 10.0 * sd
    return float(optimize.brentq(cdf, lo, hi, xtol=1e-10))


def posts_goal_status(
    goal_i48: float,
    log_median_draws: Sequence[float] | NDArray[np.float64],
    scope_posts: Sequence[ScopePost],
    *,
    as_of: datetime,
    previous: GoalStatus | None = None,
) -> GoalStatus:
    """Goal status for the last 21 originals on I48.

    ``log_median_draws``: the reach model's predictive draws of log(median I48) for the scope's
    average post at current conditions. ``scope_posts``: the scope's matured originals, oldest
    first (only the last 21 are used).
    """
    if goal_i48 <= 0:
        raise ValueError("goal must be positive")
    posts = list(scope_posts)[-SCOPE_SIZE:]
    effect = scope_effect(posts)
    base = np.asarray(log_median_draws, dtype=np.float64) + effect.mean
    if base.size == 0:
        raise ValueError("no predictive draws")
    sd = effect.sd
    log_goal = math.log(goal_i48)
    p_hi = float(np.mean(special.ndtr((base - (log_goal + EPSILON)) / sd)))
    p_lo = float(np.mean(special.ndtr(((log_goal - EPSILON) - base) / sd)))
    interval = (_mixture_quantile(base, sd, 0.1), _mixture_quantile(base, sd, 0.9))
    return _status(
        POSTS_SCOPE,
        float(goal_i48),
        p_hi,
        p_lo,
        _mixture_quantile(base, sd, 0.5),
        interval,
        as_of=as_of,
        previous=previous,
        insufficient=len(posts) < MIN_SCOPE_POSTS,
        horizon_days=None,
        detail={"n_posts": len(posts), "scope_effect_mean": effect.mean, "scope_effect_sd": effect.sd},
    )
