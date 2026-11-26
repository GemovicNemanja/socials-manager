"""Batch-mode nowcast of I48 for a post younger than 48h at a run (MODEL.md §2b, §4.1).

A post captured once at age t < 48h gets a provisional I48 estimate, on the log(1 + x) scale:

* **Fewer than 50 owner trajectories** (posts with a capture under 48h and a matured capture):
  plain precision weighting of two Gaussians: the frozen pre-publication forecast (mean and variance
  of log1p of its draws) and the capture-based estimate log1p(I_t / G(t)) from the maturity curve,
  whose variance adds the curve's thinning noise (BetaBinomial with concentration kappa) and the
  curve's own uncertainty at age t.
* **From 50 trajectories:** the MODEL §2b regression without the like-rate and increment terms
  (batch mode has no early dynamics without analytics buckets):

      log(1 + I48) = a_t + b_t log(1 + I_t) + d_t log(1 + F) + e_t log mu_pre + eps,  eps ~ t_5(0, sigma_t)

  with every coefficient and log sigma_t linear in log t (a degree-1 spline), fitted by penalised
  maximum likelihood (a weak ridge keeps log(1 + F), nearly constant for one account, identified).
  log mu_pre is the mean of log1p of the pre-publication draws.

Either way the cumulative count already seen is a hard lower bound, so the predictive is truncated
below at log1p(I_t). Outputs: quantiles, P(I48 > median_pre) (shown as "provisional", MODEL §4.1)
and the method used. ``nowcast_gate`` is MODEL §5's component gate: CRPS skill > 0 in every capture
age band (0-6h, 6-24h, 24-48h) against the pre-publication forecast alone.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy import optimize, stats

from socialmgr.modelling import maturity, scoring
from socialmgr.modelling.release import GateResult

FloatArray = NDArray[np.float64]
Method = Literal["precision_weighting", "regression"]

MIN_TRAJECTORIES = 50
NU = 5.0  # Student-t degrees of freedom of the regression errors (fixed)
RIDGE = 0.01  # penalty on the regression coefficients other than the intercept
AGE_REF_H = 12.0  # log t is centred here
DEFAULT_KAPPA = 100.0  # BetaBinomial concentration when the curve has none (the prior curve)
N_SAMPLES = 1000
QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
AGE_BANDS: tuple[tuple[float, float, str], ...] = (
    (0.0, 6.0, "0-6h"),
    (6.0, 24.0, "6-24h"),
    (24.0, 48.0, "24-48h"),
)


def age_band(age_h: float) -> str:
    for lo, hi, name in AGE_BANDS:
        if lo <= age_h < hi:
            return name
    raise ValueError(f"no nowcast for age {age_h}h (must be under 48h)")


@dataclass(frozen=True)
class PreForecast:
    """The frozen pre-publication forecast, summarised on the log1p scale (draws kept for scoring)."""

    log_mean: float
    log_sd: float
    median: float
    samples: FloatArray | None = None

    @classmethod
    def from_draws(cls, samples: FloatArray) -> "PreForecast":
        logs = np.log1p(np.maximum(samples, 0.0))
        return cls(
            log_mean=float(logs.mean()),
            log_sd=max(float(logs.std()), 1e-3),
            median=float(np.median(samples)),
            samples=np.asarray(samples, dtype=np.float64),
        )


@dataclass(frozen=True)
class Trajectory:
    """An owner post with an early capture (age < 48h) and a matured outcome."""

    post_id: str
    age_h: float
    count: int
    outcome_i48: float
    followers: int
    pre: PreForecast
    created_at: datetime | None = None
    visible_at: datetime | None = None  # when the matured capture became visible (for backtests)


@dataclass(frozen=True)
class NowcastRegression:
    coef: FloatArray  # [a0, a1, b0, b1, d0, d1, e0, e1]; x1 terms multiply log(t / 12h)
    log_sigma: FloatArray  # [s0, s1]
    nu: float
    n: int

    def predict(self, age_h: float, count: int, followers: int, pre_log_mean: float) -> tuple[float, float]:
        x = _features(np.array([age_h]), np.array([count]), np.array([followers]), np.array([pre_log_mean]))
        lt = math.log(age_h / AGE_REF_H)
        return float((x @ self.coef)[0]), math.exp(float(self.log_sigma[0] + self.log_sigma[1] * lt))


@dataclass(frozen=True)
class Nowcast:
    method: Method
    age_h: float
    age_band: str
    quantiles: dict[str, float]
    p_above_pre_median: float  # P(I48 > median of the pre-publication forecast): "provisional"
    log_loc: float
    log_scale: float
    dof: float | None  # None: Gaussian (precision weighting); else Student-t
    lower: float  # log1p(count): the truncation point
    n_trajectories: int
    provisional: bool = True

    def _dist(self) -> "_LogDist":
        return _LogDist(self.log_loc, self.log_scale, self.dof)

    def samples(self, n: int = N_SAMPLES) -> FloatArray:
        """Deterministic draws (an evenly spaced probability grid) of I48 for scoring."""
        dist = self._dist()
        f_lo = float(dist.cdf(self.lower))
        u = f_lo + (np.arange(n) + 0.5) / n * (1.0 - f_lo)
        return np.asarray(np.expm1(np.maximum(dist.ppf(u), self.lower)), dtype=np.float64)


def _features(age_h: FloatArray, count: FloatArray, followers: FloatArray, pre: FloatArray) -> FloatArray:
    lt = np.log(age_h / AGE_REF_H)
    b = np.log1p(count)
    d = np.log1p(np.maximum(followers, 0.0))
    one = np.ones_like(lt)
    return np.column_stack([one, lt, b, b * lt, d, d * lt, pre, pre * lt])


def _curve_log_sd(curve: maturity.Curve, age_h: float) -> float:
    """Uncertainty of log G(t) by the delta method in (log scale, log shape).

    The curve carries no posterior covariance, so this uses the curve's prior sds, shrunk by
    sqrt(MIN_PAIRS / n_pairs) once it was fitted from capture pairs (an approximation).
    """
    sds = np.array([maturity.LOG_SCALE_SD, maturity.LOG_SHAPE_SD])
    if curve.n_pairs > 0:
        sds = sds * math.sqrt(maturity.MIN_PAIRS / max(curve.n_pairs, maturity.MIN_PAIRS))
    h = 1e-4
    base = math.log(curve.g(age_h))
    grad = []
    for i in range(2):
        ls, lk = math.log(curve.scale_h), math.log(curve.shape)
        if i == 0:
            ls += h
        else:
            lk += h
        moved = maturity.Curve("delta", math.exp(ls), math.exp(lk))
        grad.append((math.log(moved.g(age_h)) - base) / h)
    return float(math.sqrt(float(np.sum((np.array(grad) * sds) ** 2))))


def capture_estimate(count: int, age_h: float, curve: maturity.Curve) -> tuple[float, float]:
    """(mean, variance) of the capture-based log1p(I48) estimate."""
    g = curve.g(age_h)
    if not 0 < g < 1:
        raise ValueError(f"maturity curve gives G({age_h}h) = {g}; nowcasts need 0 < G < 1")
    est = count / g
    i48 = max(est, 1.0)
    kappa = curve.kappa if curve.kappa is not None else DEFAULT_KAPPA
    # y_t | I48 ~ BetaBinomial(I48, G, kappa): var(y_t / G) / I48^2 on the log scale
    thinning = (1.0 - g) * (kappa + i48) / (i48 * g * (kappa + 1.0))
    # log1p(est) moves with log(est) scaled by est / (1 + est)
    shrink = (est / (1.0 + est)) ** 2 if est > 0 else 1.0
    return math.log1p(est), shrink * thinning + _curve_log_sd(curve, age_h) ** 2


def fit_regression(trajectories: Sequence[Trajectory]) -> NowcastRegression | None:
    """Penalised Student-t regression on the owner's trajectories (None below 50 or on failure)."""
    if len(trajectories) < MIN_TRAJECTORIES:
        return None
    age = np.array([t.age_h for t in trajectories])
    x = _features(
        age,
        np.array([float(t.count) for t in trajectories]),
        np.array([float(t.followers) for t in trajectories]),
        np.array([t.pre.log_mean for t in trajectories]),
    )
    y = np.log1p(np.array([t.outcome_i48 for t in trajectories]))
    lt = np.log(age / AGE_REF_H)
    k = x.shape[1]
    penalty = np.full(k, RIDGE)
    penalty[0] = 0.0
    beta0 = np.linalg.solve(x.T @ x + np.diag(penalty + 1e-9), x.T @ y)
    resid_sd = max(float(np.std(y - x @ beta0)), 1e-2)
    start = np.concatenate([beta0, [math.log(resid_sd), 0.0]])

    def nll(theta: FloatArray) -> float:
        beta, s0, s1 = theta[:k], theta[k], theta[k + 1]
        log_sigma = s0 + s1 * lt
        r = (y - x @ beta) / np.exp(log_sigma)
        ll = -0.5 * (NU + 1.0) * np.log1p(r * r / NU) - log_sigma
        return float(-ll.sum() + float(np.sum(penalty * beta * beta)) + 0.5 * s1 * s1)

    res = optimize.minimize(nll, start, method="BFGS", options={"maxiter": 500})
    if not np.all(np.isfinite(res.x)):
        return None
    return NowcastRegression(coef=res.x[:k], log_sigma=res.x[k:], nu=NU, n=len(trajectories))


@dataclass(frozen=True)
class _LogDist:
    """Normal (dof None) or Student-t on the log1p scale."""

    loc: float
    scale: float
    dof: float | None

    def cdf(self, x: float) -> float:
        if self.dof is None:
            return float(stats.norm.cdf(x, self.loc, self.scale))
        return float(stats.t.cdf(x, self.dof, self.loc, self.scale))

    def sf(self, x: float) -> float:
        if self.dof is None:
            return float(stats.norm.sf(x, self.loc, self.scale))
        return float(stats.t.sf(x, self.dof, self.loc, self.scale))

    def ppf(self, u: FloatArray) -> FloatArray:
        if self.dof is None:
            return np.asarray(stats.norm.ppf(u, self.loc, self.scale), dtype=np.float64)
        return np.asarray(stats.t.ppf(u, self.dof, self.loc, self.scale), dtype=np.float64)


def _truncated_quantiles(dist: _LogDist, lower: float) -> dict[str, float]:
    f_lo = float(dist.cdf(lower))
    out = {}
    for q in QUANTILES:
        v = float(dist.ppf(np.array([f_lo + q * (1.0 - f_lo)]))[0])
        out[f"q{round(q * 100):02d}"] = float(math.expm1(max(v, lower)))
    return out


def nowcast(
    count: int,
    age_h: float,
    pre: PreForecast,
    followers: int,
    curve: maturity.Curve,
    regression: NowcastRegression | None = None,
    n_trajectories: int = 0,
) -> Nowcast:
    """Provisional I48 for one capture. Pass ``regression`` (from ``fit_regression``) once the owner
    has 50 trajectories; otherwise precision weighting is used."""
    band = age_band(age_h)
    lower = math.log1p(max(count, 0))
    dof: float | None
    if regression is not None:
        method: Method = "regression"
        loc, scale = regression.predict(age_h, count, followers, pre.log_mean)
        dof = regression.nu
        dist = _LogDist(loc, scale, dof)
    else:
        method = "precision_weighting"
        cap_mean, cap_var = capture_estimate(count, age_h, curve)
        w_pre, w_cap = 1.0 / pre.log_sd**2, 1.0 / cap_var
        loc = (w_pre * pre.log_mean + w_cap * cap_mean) / (w_pre + w_cap)
        scale = math.sqrt(1.0 / (w_pre + w_cap))
        dof = None
        dist = _LogDist(loc, scale, None)
    threshold = math.log1p(pre.median)
    # P(X > threshold | X >= lower); the count already reached the median when threshold <= lower.
    p_above = 1.0 if threshold <= lower else dist.sf(threshold) / max(dist.sf(lower), 1e-300)
    return Nowcast(
        method=method,
        age_h=age_h,
        age_band=band,
        quantiles=_truncated_quantiles(dist, lower),
        p_above_pre_median=min(max(p_above, 0.0), 1.0),
        log_loc=loc,
        log_scale=scale,
        dof=dof,
        lower=lower,
        n_trajectories=n_trajectories if regression is None else regression.n,
    )


# ---------------------------------------------------------------- evaluation and gate


@dataclass(frozen=True)
class NowcastScoreRow:
    post_id: str
    age_h: float
    age_band: str
    method: Method
    crps_nowcast: float
    crps_pre: float


def _pre_samples(pre: PreForecast) -> FloatArray:
    if pre.samples is not None:
        return pre.samples
    u = (np.arange(N_SAMPLES) + 0.5) / N_SAMPLES
    return np.asarray(np.expm1(stats.norm.ppf(u, pre.log_mean, pre.log_sd)), dtype=np.float64)


def backtest_nowcast(
    trajectories: Sequence[Trajectory], curve: maturity.Curve, min_trajectories: int = MIN_TRAJECTORIES
) -> list[NowcastScoreRow]:
    """Leave-future-out scores: each trajectory is nowcast from the trajectories whose matured capture
    was visible when its early capture was taken (created_at + age), then scored on log1p CRPS against
    the pre-publication forecast alone. Trajectories without times are scored with all others."""
    rows = []
    for traj in trajectories:
        if traj.created_at is not None:
            at = traj.created_at.timestamp() + traj.age_h * 3600
            train = [
                o
                for o in trajectories
                if o.post_id != traj.post_id and o.visible_at is not None and o.visible_at.timestamp() <= at
            ]
        else:
            train = [o for o in trajectories if o.post_id != traj.post_id]
        reg = fit_regression(train) if len(train) >= min_trajectories else None
        nc = nowcast(traj.count, traj.age_h, traj.pre, traj.followers, curve, reg, len(train))
        rows.append(
            NowcastScoreRow(
                post_id=traj.post_id,
                age_h=traj.age_h,
                age_band=nc.age_band,
                method=nc.method,
                crps_nowcast=scoring.crps_log(nc.samples(), traj.outcome_i48),
                crps_pre=scoring.crps_log(_pre_samples(traj.pre), traj.outcome_i48),
            )
        )
    return rows


def nowcast_gate(rows: Sequence[NowcastScoreRow]) -> GateResult:
    """MODEL §5 nowcast gate: CRPS skill > 0 in every age band vs the pre-publication forecast alone.
    A band without scored captures fails (no evidence)."""
    parts, ok, worst = [], True, math.inf
    for _, _, name in AGE_BANDS:
        band = [r for r in rows if r.age_band == name]
        pre = sum(r.crps_pre for r in band)
        skill = 1.0 - sum(r.crps_nowcast for r in band) / pre if band and pre > 0 else math.nan
        passed = math.isfinite(skill) and skill > 0
        ok = ok and passed
        worst = min(worst, skill) if math.isfinite(skill) else worst
        parts.append(f"{name}: {'n/a' if not math.isfinite(skill) else f'{skill:.3f}'} (n={len(band)})")
    return GateResult(
        "nowcast_skill",
        ok,
        worst if math.isfinite(worst) else None,
        "CRPS skill > 0 in every age band (0-6h, 6-24h, 24-48h) vs the pre-publication forecast",
        "; ".join(parts),
    )
