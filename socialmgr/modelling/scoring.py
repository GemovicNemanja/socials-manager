"""Forecast scoring from predictive draws (MODEL.md §4.1, §5). Model-agnostic by construction."""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

SurpriseLabel = Literal["above", "as_expected", "below"]

ABOVE_PIT = 0.8
BELOW_PIT = 0.2


@dataclass(frozen=True)
class Score:
    pit_mid: float
    pit_rand: float
    crps_log: float
    in_50: bool
    in_80: bool


def pit_mid(samples: NDArray[np.float64], y: float) -> float:
    """Mid-PIT: F(y-) + ½·P(Y = y), estimated from draws rounded to counts."""
    counts = np.round(samples)
    return float(np.mean(counts < y) + 0.5 * np.mean(counts == y))


def pit_randomized(samples: NDArray[np.float64], y: float, seed: int) -> float:
    counts = np.round(samples)
    lo, eq = float(np.mean(counts < y)), float(np.mean(counts == y))
    return lo + eq * float(np.random.default_rng(seed).uniform())


def crps_log(samples: NDArray[np.float64], y: float) -> float:
    """CRPS on log(1 + x), energy form: E|X - y| - ½·E|X - X'|."""
    x = np.log1p(np.maximum(samples, 0.0))
    ly = np.log1p(max(y, 0.0))
    xs = np.sort(x)
    n = xs.size
    # E|X - X'| via the sorted-sample identity, O(n log n)
    spread = 2.0 * np.sum((2 * np.arange(1, n + 1) - n - 1) * xs) / (n * n)
    return float(np.mean(np.abs(x - ly)) - 0.5 * spread)


def score(samples: NDArray[np.float64], y: float, seed: int) -> Score:
    q10, q25, q75, q90 = np.quantile(samples, [0.1, 0.25, 0.75, 0.9])
    return Score(
        pit_mid=pit_mid(samples, y),
        pit_rand=pit_randomized(samples, y, seed),
        crps_log=crps_log(samples, y),
        in_50=bool(q25 <= y <= q75),
        in_80=bool(q10 <= y <= q90),
    )


def surprise_label(pit: float) -> SurpriseLabel:
    """MODEL §4.1 labels. These describe the post against its forecast, not whether something works."""
    if pit >= ABOVE_PIT:
        return "above"
    if pit <= BELOW_PIT:
        return "below"
    return "as_expected"
