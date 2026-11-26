# ruff: noqa: RUF002  (Greek letters follow MODEL.md notation)
"""D1 forecast-drift charts (MODEL §4.3), updated one matured observation at a time.

Every chart runs on z = Φ⁻¹(randomized PIT) under the frozen posterior predictive. Charts are immutable:
``update`` returns the next state and the alarm it raised, if any, so a run can persist the state with
``to_dict`` and resume it next run with ``from_dict``.

Deployed paging budget (K = 3 charts, about one false page per 100 days per chart). Simulated ARL0 is the
pooled mean over 36,000 NB2 replicates (μ ∈ {20, 300, 5000} or gains μ ∈ {1, 10, 60}·G, φ ∈ {1.5, 3, 8};
every (μ, φ) cell within ±3%; ``pytest -m slow tests/test_monitors.py``):

| Chart | Input | k | h upper / lower | ARL0 target | ARL0 simulated |
|---|---|---|---|---|---|
| Location, two-sided | z | 0.25 | 7.62 / 7.62 (MODEL) | 300 posts | 300.9 |
| Scale, two-sided | z² - 1 | 0.25 | 16.077 / 9.591 (solved) | 300 posts | 297.8 |
| Scale reference (MODEL, verified, not deployed) | z² - 1 | 0.25 | 11.06 / 6.94 | 200 each side | |
| Follower gains, G = 1 | z per run interval | 0.25 | 5.597 | 100 intervals | 100.0 |
| Follower gains, G = 2 | z per run interval | 0.25 | 4.418 | 50 intervals | 49.7 |
| Follower gains, G = 3 | z per run interval | 0.25 | 3.774 | 33.3 intervals | 33.4 |

Two-sided budgets put each side at 2 × ARL0 (1/ARL = 1/ARL+ + 1/ARL-). Follower thresholds are recomputed
whenever G, the owner's typical gap in days between runs, changes.

On alarm a chart records it and restarts both sides with a head start of h/2 (fast initial response,
Lucas & Crosier 1982). A chart only runs while armed (≥ 20 matured owner posts, plan §6); observations
seen while disarmed leave it untouched.

ARL1 for a 30% drop in I48 under NB2 (the predictive left at the pre-drop mean, μ = 500, 4,000 replicates
per row, seeded; ``test_arl1_for_a_30pct_drop``). The h = 5.61 column reproduces MODEL §4.4's simulation
(29.8 / 17.6 / 9.1 and P(≤ 10) 0.10 / 0.24 / 0.72); the deployed h = 7.62 is the one that pages:

| φ (log-sd) | h = 5.61: ARL1, P(≤ 10) | h = 7.62: ARL1 | P(≤ 10) | P(≤ 20) |
|---|---|---|---|---|
| 1.5 (0.97) | 29.9, 0.10 | 43.2 | 0.02 | 0.18 |
| 3 (0.63) | 17.5, 0.25 | 23.6 | 0.07 | 0.48 |
| 8 (0.37) | 8.9, 0.73 | 11.9 | 0.43 | 0.96 |

At 3 posts a day the deployed ARL1 is about 14 / 8 / 4 days. In batch mode add 48h of maturity plus up
to one run gap before an alarm can be seen.
"""

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, Self

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import special

from socialmgr.modelling.monitors.arl import (
    InControl,
    Side,
    ewma_limit_for_arl,
    threshold_for_arl,
)

Transform = Literal["z", "z2m1"]

K_REFERENCE = 0.25
LOCATION_H = 7.62  # MODEL §4.3: each side of the two-sided chart at a one-sided ARL0 of 600
LOCATION_SINGLE_H = 5.61  # single-chart reference, ARL0 = 100 two-sided
SCALE_REFERENCE_H = (11.06, 6.94)  # MODEL §4.3: upper, lower at ARL0 = 200 each (verified, not deployed)
POST_CHART_ARL0 = 300.0  # posts, two-sided (≈ 100 days at 3 posts a day)
FOLLOWER_ARL0_DAYS = 100.0
EWMA_LAMBDA = 0.1
EWMA_L = 2.148  # MODEL §4.3, ARL0 = 100 (single chart)
PIT_EPS = 1e-6
ARMING_POSTS = 20


def pit_to_z(pit: float, eps: float = PIT_EPS) -> float:
    """z = Φ⁻¹(PIT), with the PIT clipped to [eps, 1 - eps] so a PIT of 0 or 1 stays finite.

    When the PIT is estimated from S predictive draws, eps = 0.5 / (S + 1) matches its resolution.
    """
    return float(special.ndtri(min(max(pit, eps), 1.0 - eps)))


def pits_to_z(pits: ArrayLike, eps: float = PIT_EPS) -> NDArray[np.float64]:
    """Vectorised :func:`pit_to_z`."""
    return np.asarray(special.ndtri(np.clip(np.asarray(pits, dtype=np.float64), eps, 1.0 - eps)))


def chart_input(z: NDArray[np.float64], transform: Transform) -> NDArray[np.float64]:
    return z * z - 1.0 if transform == "z2m1" else z


def cusum_recursion(
    s_upper: NDArray[np.float64], s_lower: NDArray[np.float64], x: NDArray[np.float64], k: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """One step of the two one-sided CUSUMs; works on scalars or arrays (vectorised simulation)."""
    return np.maximum(0.0, s_upper + x - k), np.maximum(0.0, s_lower - x - k)


@dataclass(frozen=True)
class Alarm:
    """An alarm raised by a chart. ``index`` is the chart's observation count when it fired."""

    chart: str
    index: int
    side: str | None
    statistic: float
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)


@dataclass(frozen=True)
class CusumChart:
    """A two-sided CUSUM (an upper and a lower one-sided chart on the same input)."""

    name: str
    transform: Transform
    k: float
    h_upper: float
    h_lower: float
    target_arl0: float  # two-sided, in observations
    s_upper: float = 0.0
    s_lower: float = 0.0
    n: int = 0
    alarm_count: int = 0
    last_alarm_index: int | None = None
    last_alarm_side: Side | None = None
    armed: bool = False
    gap_days: float | None = None  # follower chart only

    @property
    def in_control_dist(self) -> InControl:
        return "chi2_1_minus_1" if self.transform == "z2m1" else "normal"

    def update(self, z: float, *, armed: bool) -> tuple[Self, Alarm | None]:
        """Feed one z; returns the next state and the alarm raised, if any."""
        if not armed:
            return replace(self, armed=False), None
        x = z * z - 1.0 if self.transform == "z2m1" else z
        su = max(0.0, self.s_upper + x - self.k)
        sl = max(0.0, self.s_lower - x - self.k)
        n = self.n + 1
        over_u, over_l = su / self.h_upper, sl / self.h_lower
        if over_u <= 1.0 and over_l <= 1.0:
            return replace(self, s_upper=su, s_lower=sl, n=n, armed=True), None
        side: Side = "upper" if over_u >= over_l else "lower"
        stat, thr = (su, self.h_upper) if side == "upper" else (sl, self.h_lower)
        alarm = Alarm(chart=self.name, index=n, side=side, statistic=stat, threshold=thr)
        nxt = replace(
            self,
            s_upper=self.h_upper / 2.0,
            s_lower=self.h_lower / 2.0,
            n=n,
            alarm_count=self.alarm_count + 1,
            last_alarm_index=n,
            last_alarm_side=side,
            armed=True,
        )
        return nxt, alarm

    def update_many(self, zs: ArrayLike, *, armed: bool) -> tuple[Self, list[Alarm]]:
        chart, alarms = self, []
        for z in np.asarray(zs, dtype=np.float64).ravel():
            chart, alarm = chart.update(float(z), armed=armed)
            if alarm is not None:
                alarms.append(alarm)
        return chart, alarms

    def with_gap(self, gap_days: float, arl0_days: float = FOLLOWER_ARL0_DAYS) -> Self:
        """Follower chart: recompute h for a new typical run gap G (ARL0 = arl0_days / G intervals).

        Statistics carry over, capped at the new thresholds so a recompute never raises an alarm itself.
        """
        target = follower_arl0(gap_days, arl0_days)
        h = threshold_for_arl(2.0 * target, self.k, "normal", "upper")
        return replace(
            self,
            h_upper=h,
            h_lower=h,
            target_arl0=target,
            gap_days=float(gap_days),
            s_upper=min(self.s_upper, h),
            s_lower=min(self.s_lower, h),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)


def location_chart(h: float = LOCATION_H, k: float = K_REFERENCE) -> CusumChart:
    """Two-sided location CUSUM on z of I48 (originals and quotes), deployed at h = 7.62 (ARL0 300)."""
    return CusumChart(name="location", transform="z", k=k, h_upper=h, h_lower=h, target_arl0=POST_CHART_ARL0)


def scale_thresholds(arl0: float = POST_CHART_ARL0, k: float = K_REFERENCE) -> tuple[float, float]:
    """(h_upper, h_lower) of the scale CUSUM on z² - 1 with each side at 2 × arl0 (two-sided arl0)."""
    return (
        threshold_for_arl(2.0 * arl0, k, "chi2_1_minus_1", "upper"),
        threshold_for_arl(2.0 * arl0, k, "chi2_1_minus_1", "lower"),
    )


def scale_chart(arl0: float = POST_CHART_ARL0, k: float = K_REFERENCE) -> CusumChart:
    """Two-sided scale CUSUM on z² - 1, thresholds solved for the deployed budget."""
    hu, hl = scale_thresholds(arl0, k)
    return CusumChart(name="scale", transform="z2m1", k=k, h_upper=hu, h_lower=hl, target_arl0=arl0)


def follower_arl0(gap_days: float, arl0_days: float = FOLLOWER_ARL0_DAYS) -> float:
    """Follower chart ARL0 in run intervals: arl0_days / G."""
    if gap_days <= 0:
        raise ValueError("gap_days must be positive")
    return arl0_days / gap_days


def follower_chart(
    gap_days: float, arl0_days: float = FOLLOWER_ARL0_DAYS, k: float = K_REFERENCE
) -> CusumChart:
    """Two-sided CUSUM on z of follower gains per run interval, at ARL0 = arl0_days / G intervals."""
    target = follower_arl0(gap_days, arl0_days)
    h = threshold_for_arl(2.0 * target, k, "normal", "upper")
    return CusumChart(
        name="follower",
        transform="z",
        k=k,
        h_upper=h,
        h_lower=h,
        target_arl0=target,
        gap_days=float(gap_days),
    )


@dataclass(frozen=True)
class EwmaChart:
    """The EWMA alternative to the location CUSUM (MODEL §4.3): E_t = (1 - λ)E_{t-1} + λ z_t.

    Alarms when |E_t| > L·σ_E, with σ_E the asymptotic sd sqrt(λ/(2-λ)) by default (the limits the ARL is
    computed for: λ = 0.1, L = 2.148 gives ARL0 = 100), or the exact time-varying sd if ``exact_limits``.
    Resets to 0 on alarm.
    """

    name: str = "ewma"
    lam: float = EWMA_LAMBDA
    limit: float = EWMA_L
    exact_limits: bool = False
    value: float = 0.0
    n: int = 0  # observations since the last (re)start
    total: int = 0
    alarm_count: int = 0
    last_alarm_index: int | None = None
    armed: bool = False

    def control_limit(self, n: int | None = None) -> float:
        steps = self.n if n is None else n
        var = self.lam / (2.0 - self.lam)
        if self.exact_limits:
            var *= 1.0 - (1.0 - self.lam) ** (2 * max(steps, 1))
        return self.limit * float(np.sqrt(var))

    def update(self, z: float, *, armed: bool) -> tuple[Self, Alarm | None]:
        if not armed:
            return replace(self, armed=False), None
        e = (1.0 - self.lam) * self.value + self.lam * z
        n, total = self.n + 1, self.total + 1
        lim = self.control_limit(n)
        if abs(e) <= lim:
            return replace(self, value=e, n=n, total=total, armed=True), None
        alarm = Alarm(
            chart=self.name, index=total, side="upper" if e > 0 else "lower", statistic=e, threshold=lim
        )
        nxt = replace(
            self,
            value=0.0,
            n=0,
            total=total,
            alarm_count=self.alarm_count + 1,
            last_alarm_index=total,
            armed=True,
        )
        return nxt, alarm

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)


def ewma_chart(arl0: float | None = None, lam: float = EWMA_LAMBDA) -> EwmaChart:
    """EWMA chart at MODEL's L = 2.148 (ARL0 100), or with L solved for ``arl0`` (e.g. 300 when deployed)."""
    limit = EWMA_L if arl0 is None else ewma_limit_for_arl(float(arl0), lam)
    return EwmaChart(lam=lam, limit=limit)
