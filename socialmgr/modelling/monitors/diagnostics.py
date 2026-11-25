# ruff: noqa: RUF002  (Greek letters follow MODEL.md notation)
"""Calibration diagnostics every 21 matured posts over the last 63 (MODEL §4.3, gates in §5).

- Berkowitz (2001) LR test: z = Φ⁻¹(PIT) against the AR(1) Gaussian z_t - μ = ρ(z_{t-1} - μ) + σε_t,
  exact likelihood, LR ~ χ²₃ under H0 (μ, σ, ρ) = (0, 1, 0).
- PIT χ² uniformity test on 10 equal bins (9 df).
- 50% and 80% central-interval coverage against exact binomial bands (central 95%).

The Wei–Held test for small counts is not implemented in v1: it needs the predictive pmf per post, which
these PIT-only diagnostics don't take. The randomized PIT keeps the other tests valid for counts.
"""

from dataclasses import asdict, dataclass
from typing import Any, Self

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import optimize, stats

from socialmgr.modelling.monitors.charts import PIT_EPS, pits_to_z

DIAGNOSTIC_EVERY = 21
DIAGNOSTIC_WINDOW = 63
GATE_P = 0.01  # MODEL §5: Berkowitz and PIT χ² p-values above 0.01
BAND_CONFIDENCE = 0.95
LOG_SD_BOUNDS = (float(np.log(1e-3)), float(np.log(1e3)))
RHO_ATANH = float(np.arctanh(0.999))


@dataclass(frozen=True)
class BerkowitzResult:
    n: int
    lr: float
    p_value: float
    mu: float
    sigma: float
    rho: float


def _ar1_loglik(z: NDArray[np.float64], mu: float, sigma: float, rho: float) -> float:
    s2 = sigma * sigma
    d = z - mu
    first = -0.5 * np.log(2 * np.pi * s2 / (1 - rho * rho)) - d[0] ** 2 * (1 - rho * rho) / (2 * s2)
    e = d[1:] - rho * d[:-1]
    rest = -0.5 * e.size * np.log(2 * np.pi * s2) - float(np.sum(e * e)) / (2 * s2)
    return float(first + rest)


def berkowitz_test(z: ArrayLike) -> BerkowitzResult:
    """Berkowitz LR test (3 df) on z = Φ⁻¹(PIT)."""
    x = np.asarray(z, dtype=np.float64)
    if x.size < 3:
        raise ValueError("Berkowitz test needs at least 3 observations")
    # Start from conditional least squares.
    y, lag = x[1:], x[:-1]
    lag_c = lag - lag.mean()
    denom = float(np.sum(lag_c * lag_c))
    rho0 = float(np.clip(np.sum(lag_c * (y - y.mean())) / denom if denom > 0 else 0.0, -0.95, 0.95))
    mu0 = float(x.mean())
    sd0 = max(float(np.std(y - rho0 * lag - (1 - rho0) * mu0)), float(np.exp(LOG_SD_BOUNDS[0])))

    def unpack(theta: NDArray[np.float64]) -> tuple[float, float, float]:
        # Bounded so a degenerate window (e.g. a constant z) gives a huge but finite LR.
        log_sd = float(np.clip(theta[1], LOG_SD_BOUNDS[0], LOG_SD_BOUNDS[1]))
        return (
            float(theta[0]),
            float(np.exp(log_sd)),
            float(np.tanh(np.clip(theta[2], -RHO_ATANH, RHO_ATANH))),
        )

    def nll(theta: NDArray[np.float64]) -> float:
        return -_ar1_loglik(x, *unpack(theta))

    best = optimize.minimize(
        nll,
        np.array([mu0, np.log(sd0), np.arctanh(rho0)]),
        method="Nelder-Mead",
        options={"xatol": 1e-7, "fatol": 1e-9, "maxiter": 4000},
    )
    mu, sigma, rho = unpack(best.x)
    ll1 = max(-float(best.fun), _ar1_loglik(x, mu0, sd0, rho0))
    ll0 = _ar1_loglik(x, 0.0, 1.0, 0.0)
    lr = max(0.0, 2.0 * (ll1 - ll0))
    return BerkowitzResult(
        n=int(x.size), lr=lr, p_value=float(stats.chi2.sf(lr, 3)), mu=mu, sigma=sigma, rho=rho
    )


@dataclass(frozen=True)
class UniformityResult:
    n: int
    chi2: float
    p_value: float
    counts: tuple[int, ...]


def pit_uniformity_test(pit: ArrayLike, bins: int = 10) -> UniformityResult:
    """Pearson χ² test that the PIT is uniform on ``bins`` equal bins (bins - 1 df)."""
    u = np.clip(np.asarray(pit, dtype=np.float64), 0.0, 1.0)
    counts = np.bincount(np.minimum((u * bins).astype(int), bins - 1), minlength=bins)
    expected = u.size / bins
    chi2 = float(np.sum((counts - expected) ** 2) / expected)
    return UniformityResult(
        n=int(u.size),
        chi2=chi2,
        p_value=float(stats.chi2.sf(chi2, bins - 1)),
        counts=tuple(int(c) for c in counts),
    )


def binomial_band(n: int, level: float, confidence: float = BAND_CONFIDENCE) -> tuple[float, float]:
    """Exact central binomial band for the coverage fraction of a ``level`` interval over n forecasts.

    [q_lo / n, q_hi / n] with q_lo, q_hi the (1 - c)/2 and (1 + c)/2 quantiles of Binomial(n, level).
    For n = 60 and level 0.8 this is [0.70, 0.90].
    """
    tail = (1.0 - confidence) / 2.0
    lo = float(stats.binom.ppf(tail, n, level))
    hi = float(stats.binom.ppf(1.0 - tail, n, level))
    return lo / n, hi / n


@dataclass(frozen=True)
class CoverageResult:
    level: float
    n: int
    hits: int
    observed: float
    band_lo: float
    band_hi: float
    inside: bool


def coverage_check(pit: ArrayLike, level: float) -> CoverageResult:
    """Coverage of the central ``level`` interval: a hit when (1 - level)/2 ≤ PIT ≤ (1 + level)/2."""
    u = np.asarray(pit, dtype=np.float64)
    a = (1.0 - level) / 2.0
    hits = int(np.sum((u >= a) & (u <= 1.0 - a)))
    n = int(u.size)
    lo, hi = binomial_band(n, level)
    observed = hits / n
    return CoverageResult(
        level=level, n=n, hits=hits, observed=observed, band_lo=lo, band_hi=hi, inside=lo <= observed <= hi
    )


@dataclass(frozen=True)
class Diagnostics:
    at_post: int  # matured posts seen when the diagnostics ran
    n: int  # window size
    berkowitz: BerkowitzResult
    uniformity: UniformityResult
    coverage_50: CoverageResult
    coverage_80: CoverageResult

    @property
    def passed(self) -> bool:
        """The MODEL §5 calibration gates: both p-values above 0.01 and both coverages inside their bands."""
        return (
            self.berkowitz.p_value > GATE_P
            and self.uniformity.p_value > GATE_P
            and self.coverage_50.inside
            and self.coverage_80.inside
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        u = data["uniformity"]
        return cls(
            at_post=data["at_post"],
            n=data["n"],
            berkowitz=BerkowitzResult(**data["berkowitz"]),
            uniformity=UniformityResult(
                n=u["n"], chi2=u["chi2"], p_value=u["p_value"], counts=tuple(u["counts"])
            ),
            coverage_50=CoverageResult(**data["coverage_50"]),
            coverage_80=CoverageResult(**data["coverage_80"]),
        )


def run_diagnostics(
    pit: ArrayLike, *, at_post: int, window: int = DIAGNOSTIC_WINDOW, eps: float = PIT_EPS
) -> Diagnostics:
    """All diagnostics on the last ``window`` randomized PITs."""
    u = np.asarray(pit, dtype=np.float64)[-window:]
    return Diagnostics(
        at_post=at_post,
        n=int(u.size),
        berkowitz=berkowitz_test(pits_to_z(u, eps)),
        uniformity=pit_uniformity_test(u),
        coverage_50=coverage_check(u, 0.5),
        coverage_80=coverage_check(u, 0.8),
    )


def diagnostics_due(posts_seen: int, last_at: int, every: int = DIAGNOSTIC_EVERY) -> bool:
    """True when the matured-post count has crossed a multiple of ``every`` since the last diagnostics."""
    return posts_seen // every > last_at // every
