"""Follower baselines (MODEL.md §2c "Baselines", §5: 7- and 30-day CRPS skill is measured against them).

Both read only the follower count at each run, so they work from the first two runs on.
- **Persistence + drift**: F_T = F_now + drift·h with drift the trailing-window slope; Gaussian
  random-walk noise with per-day variance estimated from the run increments, plus the drift's
  estimation error. Biased low on convex paths [REV].
- **Local linear trend**: level + slope Kalman filter on irregular run times; the level and slope
  noise variances are maximum-likelihood estimates.
Each returns integer predictive draws per horizon, scored like the model (``score_follower_forecast``).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from scipy import optimize

from socialmgr.modelling.followers.data import SECONDS_PER_DAY


@dataclass(frozen=True)
class BaselineForecast:
    name: str
    mean: dict[int, float]
    sd: dict[int, float]
    draws: dict[int, NDArray[np.int64]]

    def to_dict(self, include_draws: bool = False) -> dict[str, object]:
        out: dict[str, object] = {
            "name": self.name,
            "mean": {str(k): v for k, v in self.mean.items()},
            "sd": {str(k): v for k, v in self.sd.items()},
        }
        if include_draws:
            out["draws"] = {str(k): [int(x) for x in v] for k, v in self.draws.items()}
        return out


def _days(times: Sequence[datetime]) -> NDArray[np.float64]:
    t0 = times[0]
    return np.array([(t - t0).total_seconds() / SECONDS_PER_DAY for t in times])


def _draws(mean: float, sd: float, n: int, rng: np.random.Generator) -> NDArray[np.int64]:
    return np.asarray(np.maximum(np.rint(mean + sd * rng.standard_normal(n)), 0), dtype=np.int64)


def persistence_drift(
    times: Sequence[datetime],
    followers: Sequence[int],
    horizons: Sequence[int] = (7, 30),
    window_days: float = 28.0,
    n_draws: int = 4000,
    seed: int = 0,
) -> BaselineForecast:
    if len(times) < 2:
        raise ValueError("persistence + drift needs at least two runs")
    t = _days(times)
    f = np.asarray(followers, dtype=float)
    keep = t >= t[-1] - window_days
    if keep.sum() < 2:
        keep[-2:] = True
    tw, fw = t[keep], f[keep]
    span = float(tw[-1] - tw[0])
    drift = float(fw[-1] - fw[0]) / span if span > 0 else 0.0
    dt = np.diff(tw)
    resid = np.diff(fw) - drift * dt
    var_day = float(np.sum(resid**2) / np.sum(dt)) if dt.size and np.sum(dt) > 0 else 1.0
    var_day = max(var_day, 1e-6)
    rng = np.random.default_rng(seed)
    mean: dict[int, float] = {}
    sd: dict[int, float] = {}
    draws: dict[int, NDArray[np.int64]] = {}
    for h in horizons:
        mean[h] = float(f[-1] + drift * h)
        drift_var = var_day / span if span > 0 else 0.0
        sd[h] = math.sqrt(var_day * h + drift_var * h * h)
        draws[h] = _draws(mean[h], sd[h], n_draws, rng)
    return BaselineForecast("persistence_drift", mean, sd, draws)


def _llt_filter(
    t: NDArray[np.float64], f: NDArray[np.float64], q_level: float, q_slope: float, r_obs: float
) -> tuple[float, NDArray[np.float64], NDArray[np.float64]]:
    """Kalman filter for a local linear trend on irregular times; returns (loglik, state, cov)."""
    x = np.array([f[0], 0.0])
    big = 1e6 * max(1.0, float(np.var(f)))
    p = np.diag([r_obs, big])
    ll = 0.0
    for k in range(1, t.size):
        dt = float(t[k] - t[k - 1])
        trans = np.array([[1.0, dt], [0.0, 1.0]])
        q = np.array(
            [
                [q_level * dt + q_slope * dt**3 / 3.0, q_slope * dt**2 / 2.0],
                [q_slope * dt**2 / 2.0, q_slope * dt],
            ]
        )
        x = trans @ x
        p = trans @ p @ trans.T + q
        s = float(p[0, 0] + r_obs)
        innov = float(f[k] - x[0])
        if k >= 2:  # the first increment initialises the slope
            ll += -0.5 * (math.log(2.0 * math.pi * s) + innov * innov / s)
        gain = p[:, 0] / s
        x = x + gain * innov
        p = p - np.outer(gain, p[0, :])
    return ll, x, p


def local_linear_trend(
    times: Sequence[datetime],
    followers: Sequence[int],
    horizons: Sequence[int] = (7, 30),
    n_draws: int = 4000,
    seed: int = 0,
) -> BaselineForecast:
    if len(times) < 3:
        raise ValueError("a local linear trend needs at least three runs")
    t = _days(times)
    f = np.asarray(followers, dtype=float)
    r_obs = 0.25  # counts are exact up to rounding
    scale = max(float(np.var(np.diff(f) / np.maximum(np.diff(t), 1e-6))), 1.0)

    def neg(v: NDArray[np.float64]) -> float:
        return -_llt_filter(t, f, scale * math.exp(v[0]), scale * math.exp(v[1]), r_obs)[0]

    res = optimize.minimize(
        neg, np.array([0.0, -3.0]), method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-6}
    )
    q_level, q_slope = scale * math.exp(res.x[0]), scale * math.exp(res.x[1])
    _, x, p = _llt_filter(t, f, q_level, q_slope, r_obs)
    rng = np.random.default_rng(seed)
    mean: dict[int, float] = {}
    sd: dict[int, float] = {}
    draws: dict[int, NDArray[np.int64]] = {}
    for h in horizons:
        trans = np.array([[1.0, float(h)], [0.0, 1.0]])
        q = np.array(
            [
                [q_level * h + q_slope * h**3 / 3.0, q_slope * h**2 / 2.0],
                [q_slope * h**2 / 2.0, q_slope * h],
            ]
        )
        xh = trans @ x
        ph = trans @ p @ trans.T + q
        mean[h] = float(xh[0])
        sd[h] = math.sqrt(max(float(ph[0, 0]), 1e-9))
        draws[h] = _draws(mean[h], sd[h], n_draws, rng)
    return BaselineForecast("local_linear_trend", mean, sd, draws)
