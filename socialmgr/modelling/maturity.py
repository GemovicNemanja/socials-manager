"""Maturity curve G(a) = E[y(a)] / E[y(48h)] (MODEL.md §1.3).

The family is a Weibull CDF normalised so G(48h) = 1; it keeps a small positive slope after 48h.
The prior puts half of 72h impressions at ~80 minutes and ~94% at 24h (MODEL §1.3 prior).

The curve is fitted from repeated captures of the same post: a capture y1 at age a1 and the next
capture y2 at a2 > a1 give y1 | y2 ~ BetaBinomial(y2, G(a1)/G(a2), kappa). Every fitted curve is
versioned, and each forecast uses the version frozen with it.
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import optimize, special

PRIOR_SCALE_H = 3.0243
PRIOR_SHAPE = 0.46878
PRIMARY_AGE_H = 48.0
PRIOR_CURVE_VERSION = "prior-weibull-1"
FAMILY = "weibull"

MIN_PAIRS = 30  # fewer capture pairs than this keep the prior curve
# Priors on the fitted parameters (assumptions around the MODEL §1.3 prior shape).
LOG_SCALE_SD = 0.75
LOG_SHAPE_SD = 0.3
KAPPA_SD = 200.0  # MODEL §1.3: kappa ~ half-N(0, 200^2)
LOG_G7D_SD = 0.1  # MODEL §1.3: half-N(0.1) on log G(7d)


@dataclass(frozen=True)
class Curve:
    version: str
    scale_h: float
    shape: float
    kappa: float | None = None
    n_pairs: int = 0

    def _cdf(self, age_h: float) -> float:
        if age_h <= 0:
            return 0.0
        return -math.expm1(-((age_h / self.scale_h) ** self.shape))

    def g(self, age_h: float) -> float:
        """G(age); G(48h) = 1."""
        return self._cdf(age_h) / self._cdf(PRIMARY_AGE_H)

    def estimate_i48(self, observed: int, age_h: float) -> float | None:
        """Map a count observed at ``age_h`` to the 48h scale."""
        g = self.g(age_h)
        if g <= 0:
            return None
        return observed / g

    @property
    def params(self) -> dict[str, float]:
        out = {"scale_h": self.scale_h, "shape": self.shape}
        if self.kappa is not None:
            out["kappa"] = self.kappa
        return out


PRIOR = Curve(version=PRIOR_CURVE_VERSION, scale_h=PRIOR_SCALE_H, shape=PRIOR_SHAPE)


def prior_curve(age_h: float) -> float:
    """G(age) under the prior."""
    return PRIOR.g(age_h)


def estimate_i48(observed: int, age_h: float, curve: Curve = PRIOR) -> float | None:
    return curve.estimate_i48(observed, age_h)


@dataclass(frozen=True)
class CapturePair:
    a1_h: float
    y1: int
    a2_h: float
    y2: int


def pairs_from_captures(captures: Sequence[tuple[float, int]]) -> list[CapturePair]:
    """Consecutive (age, count) captures of one post, each earlier capture used once (MODEL §1.3)."""
    ordered = sorted((a, y) for a, y in captures if a > 0 and y is not None)
    pairs = []
    for (a1, y1), (a2, y2) in itertools.pairwise(ordered):
        if a2 > a1 and y2 > 0 and 0 <= y1 <= y2:
            pairs.append(CapturePair(a1, y1, a2, y2))
    return pairs


def _weibull_cdf(age: np.ndarray, scale: float, shape: float) -> np.ndarray:
    return -np.expm1(-np.power(age / scale, shape))


def _neg_log_posterior(
    theta: np.ndarray, a1: np.ndarray, y1: np.ndarray, a2: np.ndarray, y2: np.ndarray
) -> float:
    log_scale, log_shape, log_kappa = theta
    scale, shape, kappa = math.exp(log_scale), math.exp(log_shape), math.exp(log_kappa)
    p = _weibull_cdf(a1, scale, shape) / _weibull_cdf(a2, scale, shape)
    p = np.clip(p, 1e-9, 1 - 1e-9)
    alpha, beta = p * kappa, (1 - p) * kappa
    # BetaBinomial log-pmf without the binomial coefficient (constant in theta).
    ll = special.betaln(y1 + alpha, y2 - y1 + beta) - special.betaln(alpha, beta)
    log_g7 = math.log(
        float(
            _weibull_cdf(np.array([168.0]), scale, shape)[0]
            / _weibull_cdf(np.array([PRIMARY_AGE_H]), scale, shape)[0]
        )
    )
    lp = (
        -0.5 * ((log_scale - math.log(PRIOR_SCALE_H)) / LOG_SCALE_SD) ** 2
        - 0.5 * ((log_shape - math.log(PRIOR_SHAPE)) / LOG_SHAPE_SD) ** 2
        - 0.5 * (kappa / KAPPA_SD) ** 2
        + log_kappa  # Jacobian of the log transform
        - 0.5 * (log_g7 / LOG_G7D_SD) ** 2
    )
    return -(float(np.sum(ll)) + float(lp))


def fit(pairs: Sequence[CapturePair]) -> Curve:
    """MAP fit of the curve from capture pairs; the prior curve when there are too few."""
    informative = [p for p in pairs if p.a1_h < PRIMARY_AGE_H]
    if len(informative) < MIN_PAIRS:
        return PRIOR
    a1 = np.array([p.a1_h for p in pairs])
    y1 = np.array([p.y1 for p in pairs], dtype=float)
    a2 = np.array([p.a2_h for p in pairs])
    y2 = np.array([p.y2 for p in pairs], dtype=float)
    start = np.array([math.log(PRIOR_SCALE_H), math.log(PRIOR_SHAPE), math.log(100.0)])
    result = optimize.minimize(
        _neg_log_posterior,
        start,
        args=(a1, y1, a2, y2),
        method="L-BFGS-B",
        bounds=[
            (math.log(0.05), math.log(48.0)),
            (math.log(0.1), math.log(2.0)),
            (math.log(1.0), math.log(5000.0)),
        ],
    )
    if not result.success:
        return PRIOR
    scale, shape, kappa = (float(math.exp(v)) for v in result.x)
    scale, shape, kappa = round(scale, 4), round(shape, 4), round(kappa, 1)
    return Curve(
        version=f"{FAMILY}-{scale:.4f}h-{shape:.4f}",
        scale_h=scale,
        shape=shape,
        kappa=kappa,
        n_pairs=len(pairs),
    )
