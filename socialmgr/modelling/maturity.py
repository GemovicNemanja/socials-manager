"""Maturity curve G(a) = E[y(a)] / E[y(48h)] (MODEL.md §1.3).

M0 ships only the prior shape. M1 fits the curve from repeated captures.
The prior is a Weibull CDF with half of 72h impressions by ~80 minutes and ~94% by 24h
(MODEL §1.3 prior), normalised so G(48h) = 1. It keeps a small positive slope after 48h.
"""

import math

PRIOR_SCALE_H = 3.0243
PRIOR_SHAPE = 0.46878
PRIMARY_AGE_H = 48.0
PRIOR_CURVE_VERSION = "prior-weibull-1"


def _weibull_cdf(age_h: float) -> float:
    if age_h <= 0:
        return 0.0
    return 1.0 - math.exp(-((age_h / PRIOR_SCALE_H) ** PRIOR_SHAPE))


def prior_curve(age_h: float) -> float:
    """G(age) under the prior; G(48h) = 1."""
    return _weibull_cdf(age_h) / _weibull_cdf(PRIMARY_AGE_H)


def estimate_i48(observed: int, age_h: float) -> float | None:
    """Map a count observed at ``age_h`` to the 48h scale with the prior curve."""
    g = prior_curve(age_h)
    if g <= 0:
        return None
    return observed / g
