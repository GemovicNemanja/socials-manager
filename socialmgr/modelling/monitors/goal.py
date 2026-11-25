"""D2: the goal is wrong (MODEL §4.3; plan §6 drops the peer-percentile check in v1).

Runs only while D1 is quiet and from 60 matured owner posts (plan §6 eligibility). Over the last 30 posts:

- π̄ = mean_i P(y_i ≥ G) under each post's frozen predictive, i.e. P(Y ≥ G) under their equal-weight mixture.
- π̄ < 0.10 (too hard): propose the mixture's 60th-70th percentile, suggesting the 65th.
  π̄ > 0.90 (too easy): propose the 70th-80th, suggesting the 75th.
- The proposal also needs an exact two-sided Poisson-binomial hit-count test at p < 0.05, with the hits
  on the side π̄ points to.

The test's null hypothesis (an interpretation; MODEL names the test but not its null): *the goal is set at
the typical level*, meaning each post would clear it with probability p⁰_i = P(y_i ≥ G_med), where G_med is
the mixture median (so mean p⁰_i = 0.5). The observed hits H = #{y_i ≥ G} are compared with the
Poisson-binomial(p⁰) by the minimum-likelihood two-sided p-value. This requires the outcomes, not just the
model, to show the goal is hit far less (or more) often than a typical-level goal would be. A null at the
band edge (rate 0.10) cannot reject at n = 30 even with zero hits, so it is not used. The calibration
p-value of H against the model's own P(y_i ≥ G) is reported for information only.
"""

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, Self

import numpy as np
from numpy.typing import ArrayLike, NDArray

D2_WINDOW = 30
D2_MIN_POSTS = 60
D2_LOW, D2_HIGH = 0.10, 0.90
D2_P = 0.05
HARD_RANGE = (0.60, 0.65, 0.70)  # (lo, suggestion, hi) predictive percentiles when the goal is too hard
EASY_RANGE = (0.70, 0.75, 0.80)  # ... when it is too easy

GoalStatus = Literal[
    "skipped_d1_alarm",  # D1 is not quiet
    "insufficient_posts",
    "goal_ok",  # 0.10 ≤ π̄ ≤ 0.90
    "not_significant",  # π̄ outside the band but the hit-count test doesn't confirm it
    "too_hard",  # proposal made
    "too_easy",  # proposal made
]


def poisson_binomial_pmf(probs: ArrayLike) -> NDArray[np.float64]:
    """pmf of the number of successes among independent Bernoulli(p_i), by dynamic programming."""
    pmf = np.zeros(1)
    pmf[0] = 1.0
    for p in np.asarray(probs, dtype=np.float64).ravel():
        nxt = np.empty(pmf.size + 1)
        nxt[0] = pmf[0] * (1.0 - p)
        nxt[1:-1] = pmf[1:] * (1.0 - p) + pmf[:-1] * p
        nxt[-1] = pmf[-1] * p
        pmf = nxt
    return pmf


def poisson_binomial_test(hits: int, probs: ArrayLike) -> float:
    """Exact two-sided p-value: total probability of outcomes no more likely than the observed count."""
    pmf = poisson_binomial_pmf(probs)
    if not 0 <= hits < pmf.size:
        raise ValueError("hits out of range")
    return float(min(1.0, pmf[pmf <= pmf[hits] * (1.0 + 1e-7)].sum()))


@dataclass(frozen=True)
class GoalCheck:
    status: GoalStatus
    goal: float
    n: int
    pi_bar: float | None = None
    hits: int | None = None
    expected_hits: float | None = None  # under the model: Σ P(y_i ≥ G)
    null_hits: float | None = None  # under the typical-level null: Σ p⁰_i
    p_value: float | None = None  # hit-count test against the typical-level null
    calibration_p: float | None = None  # information only: hits against the model's own P(y_i ≥ G)
    suggestion: float | None = None
    range_lo: float | None = None
    range_hi: float | None = None
    suggestion_percentile: float | None = None

    @property
    def proposed(self) -> bool:
        return self.status in ("too_hard", "too_easy")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "proposed": self.proposed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**{k: v for k, v in data.items() if k != "proposed"})


def check_goal(
    draws: ArrayLike,
    outcomes: ArrayLike,
    goal: float,
    *,
    d1_quiet: bool,
    window: int = D2_WINDOW,
) -> GoalCheck:
    """D2 on the last ``window`` matured posts.

    ``draws`` is (posts × draws) of each post's frozen predictive (as stored in ``forecast_draws``), in
    maturity order; ``outcomes`` the matured values y_i in the same order.
    """
    if not d1_quiet:
        return GoalCheck(status="skipped_d1_alarm", goal=goal, n=0)
    d = np.round(np.atleast_2d(np.asarray(draws, dtype=np.float64)))
    y = np.asarray(outcomes, dtype=np.float64).ravel()
    if d.shape[0] != y.size:
        raise ValueError("draws and outcomes must have one row per post")
    if y.size < window:
        return GoalCheck(status="insufficient_posts", goal=goal, n=int(y.size))
    d, y = d[-window:], y[-window:]
    p = np.mean(d >= goal, axis=1)
    pi_bar = float(p.mean())
    hits = int(np.sum(y >= goal))
    pooled = d.ravel()
    g_med = float(np.quantile(pooled, 0.5))
    p0 = np.mean(d >= g_med, axis=1)
    p_value = poisson_binomial_test(hits, p0)
    base = GoalCheck(
        status="goal_ok",
        goal=goal,
        n=int(y.size),
        pi_bar=pi_bar,
        hits=hits,
        expected_hits=float(p.sum()),
        null_hits=float(p0.sum()),
        p_value=p_value,
        calibration_p=poisson_binomial_test(hits, p),
    )
    if D2_LOW <= pi_bar <= D2_HIGH:
        return base
    too_hard = pi_bar < D2_LOW
    right_side = hits < p0.sum() if too_hard else hits > p0.sum()
    if p_value >= D2_P or not right_side:
        return replace(base, status="not_significant")
    lo, mid, hi = HARD_RANGE if too_hard else EASY_RANGE
    q_lo, q_mid, q_hi = (float(v) for v in np.quantile(pooled, [lo, mid, hi]))
    return replace(
        base,
        status="too_hard" if too_hard else "too_easy",
        suggestion=q_mid,
        range_lo=q_lo,
        range_hi=q_hi,
        suggestion_percentile=mid,
    )
