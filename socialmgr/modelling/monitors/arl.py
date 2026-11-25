# ruff: noqa: RUF002  (Greek letters follow MODEL.md notation)
"""Average run lengths by Markov chain (Brook & Evans 1972), and thresholds solved from them (MODEL §4.3).

A one-sided upper CUSUM S_n = max(0, S_{n-1} + X_n - k) alarms when S_n > h. The interval [0, h] is cut
into ``n_states`` cells of width w = 2h / (2m - 1): cell 0 is [0, w/2) (it holds the atom at zero) and
cell j ≥ 1 is [(j - ½)w, (j + ½)w), so the top cell ends exactly at h. Each cell is represented by its
centre jw; the transient matrix is Q[i, j] = P(cell j | centre i) and the ARL from cell i is
((I - Q)^{-1} 1)_i. A lower CUSUM on X is the upper CUSUM on -X.

Verified against MODEL §4.3 (k = 0.25, 800 states):

| Chart | MODEL h | ARL at MODEL h | h solved for the target |
|---|---|---|---|
| Normal, one side at 600 | 7.62 | 603.1 | 7.610 |
| Normal, one side at 200 (two-sided 100) | 5.61 | 201.4 | 5.597 |
| χ²₁ − 1 upper at 200 | 11.06 | 198.8 | 11.084 |
| χ²₁ − 1 lower at 200 | 6.94 | 201.8 | 6.923 |

All four agree to the second decimal of h (≤ 0.025), i.e. within rounding of a two-significant-figure
ARL target (≤ 1% in ARL). The deployed numbers are the solved ones, stored with the model version.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy import optimize, special, stats

Side = Literal["upper", "lower"]
InControl = Literal["normal", "chi2_1_minus_1"]
Cdf = Callable[[NDArray[np.float64]], NDArray[np.float64]]

DEFAULT_STATES = 800
EWMA_STATES = 801  # odd, so one cell is centred on zero


def increment_cdf(dist: InControl | Cdf, side: Side) -> Cdf:
    """CDF of the CUSUM increment: X for the upper side, -X for the lower side.

    ``dist`` is the in-control distribution of the chart input X: ``"normal"`` (z), ``"chi2_1_minus_1"``
    (z² - 1, the scale chart) or any continuous CDF of X.
    """
    if dist == "normal":
        return lambda x: np.asarray(special.ndtr(x), dtype=np.float64)
    if dist == "chi2_1_minus_1":
        if side == "upper":
            return lambda x: np.asarray(stats.chi2.cdf(x + 1.0, 1), dtype=np.float64)
        return lambda x: np.asarray(stats.chi2.sf(1.0 - x, 1), dtype=np.float64)
    cdf = dist
    if isinstance(cdf, str):
        raise ValueError(f"unknown in-control distribution {cdf!r}")
    if side == "upper":
        return cdf
    return lambda x: 1.0 - cdf(-x)


def _arl_vector(h: float, k: float, cdf: Cdf, n_states: int) -> tuple[NDArray[np.float64], float]:
    m = n_states
    w = 2.0 * h / (2 * m - 1)
    # F[i, j] = P(S' < upper edge of cell j | centre i) = cdf((j - i + ½)w + k): Toeplitz, 2m - 1 values.
    offsets = np.arange(-(m - 1), m, dtype=np.float64)
    f = cdf((offsets + 0.5) * w + k)
    idx = np.arange(m)[None, :] - np.arange(m)[:, None] + (m - 1)
    big_f = f[idx]
    q = np.empty((m, m))
    q[:, 0] = big_f[:, 0]
    q[:, 1:] = np.diff(big_f, axis=1)
    arl = np.linalg.solve(np.eye(m) - q, np.ones(m))
    return arl, w


def cusum_arl(
    h: float,
    k: float,
    dist: InControl | Cdf = "normal",
    side: Side = "upper",
    *,
    start: float = 0.0,
    n_states: int = DEFAULT_STATES,
) -> float:
    """ARL of a one-sided CUSUM (reference k, threshold h) started at ``start`` (0, or h/2 for a FIR)."""
    if h <= 0:
        raise ValueError("h must be positive")
    arl, w = _arl_vector(h, k, increment_cdf(dist, side), n_states)
    cell = min(round(start / w), n_states - 1)
    return float(arl[cell])


def two_sided_arl(arl_upper: float, arl_lower: float) -> float:
    """ARL of two one-sided charts run together: 1/ARL = 1/ARL+ + 1/ARL-."""
    return 1.0 / (1.0 / arl_upper + 1.0 / arl_lower)


def threshold_for_arl(
    target: float,
    k: float,
    dist: InControl | Cdf = "normal",
    side: Side = "upper",
    *,
    n_states: int = DEFAULT_STATES,
    xtol: float = 1e-5,
) -> float:
    """The h at which the one-sided chart's zero-start ARL equals ``target`` (Brent's root-finder)."""
    if isinstance(dist, str):
        return _threshold_cached(float(target), float(k), dist, side, n_states, xtol)
    return _solve_threshold(target, k, dist, side, n_states, xtol)


@lru_cache(maxsize=256)
def _threshold_cached(
    target: float, k: float, dist: InControl, side: Side, n_states: int, xtol: float
) -> float:
    return _solve_threshold(target, k, dist, side, n_states, xtol)


def _solve_threshold(
    target: float, k: float, dist: InControl | Cdf, side: Side, n_states: int, xtol: float
) -> float:
    def gap(h: float) -> float:
        return cusum_arl(h, k, dist, side, n_states=n_states) - target

    lo = 1e-3
    if gap(lo) >= 0:
        raise ValueError(f"target ARL {target} is below the chart's minimum run length")
    hi = 1.0
    while gap(hi) < 0:
        lo, hi = hi, hi * 2.0
        if hi > 1e4:
            raise ValueError(f"no threshold below 1e4 reaches ARL {target}")
    return float(optimize.brentq(gap, lo, hi, xtol=xtol))


def ewma_arl(lam: float, limit: float, *, n_states: int = EWMA_STATES) -> float:
    """Zero-start ARL of a two-sided EWMA on N(0, 1) input with asymptotic limits ±L·sqrt(λ/(2-λ))."""
    c = limit * float(np.sqrt(lam / (2.0 - lam)))
    w = 2.0 * c / n_states
    centres = -c + (np.arange(n_states) + 0.5) * w
    edges = -c + np.arange(n_states + 1) * w
    lo = (edges[None, :-1] - (1.0 - lam) * centres[:, None]) / lam
    hi = (edges[None, 1:] - (1.0 - lam) * centres[:, None]) / lam
    q = special.ndtr(hi) - special.ndtr(lo)
    arl = np.linalg.solve(np.eye(n_states) - q, np.ones(n_states))
    return float(arl[n_states // 2])


@lru_cache(maxsize=64)
def ewma_limit_for_arl(target: float, lam: float) -> float:
    """The L at which the two-sided EWMA's zero-start ARL equals ``target``."""
    return float(optimize.brentq(lambda lim: ewma_arl(lam, lim) - target, 0.5, 6.0, xtol=1e-5))
