"""Scoring a frozen follower forecast once its horizon passes (MODEL.md §4.1, §5; plan §6).

The Account screen shows past follower growth against what was expected for that period: the
observed F at the horizon against the frozen draws of F made at the start of the period.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from socialmgr.modelling.scoring import SurpriseLabel, crps_log, pit_mid, surprise_label


@dataclass(frozen=True)
class FollowerScore:
    horizon_days: int
    observed: int
    followers_at_forecast: int | None
    expected_median: float
    expected_q10: float
    expected_q90: float
    pit_mid: float
    crps_log: float  # CRPS on log(1 + F)
    outcome: SurpriseLabel  # above / as_expected / below, MODEL §4.1 thresholds 0.8 / 0.2
    in_50: bool
    in_80: bool

    @property
    def expected_growth(self) -> float | None:
        if self.followers_at_forecast is None:
            return None
        return self.expected_median - self.followers_at_forecast

    @property
    def observed_growth(self) -> int | None:
        if self.followers_at_forecast is None:
            return None
        return self.observed - self.followers_at_forecast

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FollowerScore":
        return cls(**d)


def score_follower_forecast(
    draws: Sequence[int] | NDArray[np.int64],
    observed: int,
    *,
    horizon_days: int,
    followers_at_forecast: int | None = None,
) -> FollowerScore:
    """Score frozen draws of F at the horizon against the observed F (the run nearest the horizon)."""
    x = np.asarray(draws, dtype=np.float64)
    if x.size == 0:
        raise ValueError("no draws to score")
    q10, q25, q50, q75, q90 = np.quantile(x, [0.1, 0.25, 0.5, 0.75, 0.9])
    pit = pit_mid(x, float(observed))
    return FollowerScore(
        horizon_days=horizon_days,
        observed=int(observed),
        followers_at_forecast=followers_at_forecast,
        expected_median=float(q50),
        expected_q10=float(q10),
        expected_q90=float(q90),
        pit_mid=pit,
        crps_log=crps_log(x, float(observed)),
        outcome=surprise_label(pit),
        in_50=bool(q25 <= observed <= q75),
        in_80=bool(q10 <= observed <= q90),
    )
