# ruff: noqa: RUF002  (Greek letters follow MODEL.md notation)
"""D3: adaptive conformal inference on the PIT (Gibbs & Candès 2021; MODEL §4.3).

The target is the central 80% interval (α = 0.2). At post t the interval is the predictive's
[α_t/2, 1 - α_t/2] quantiles, so it misses when PIT < α_t/2 or PIT > 1 - α_t/2 (err_t = 1). Then
α_{t+1} = clip(α_t + γ(α - err_t), 0.01, 0.6) with γ = 0.05. A falling α_t means the intervals have to
widen to keep 80% coverage; the alarm fires when α_t < 0.10 for 20 consecutive posts (once per streak).

AgACI (aggregating γ ∈ {0.01, 0.02, 0.05, 0.1}) is not implemented in v1; γ = 0.05 is used.

In control (randomized PIT exactly uniform; 1,000 NB2 replicates, ``test_aci_in_control_run_length``) the
mean run length to a false D3 alarm is about 12,800 posts (median 8,900): D3 has no paging budget of its
own in MODEL, and it is far quieter than the post charts' 300.
"""

from dataclasses import asdict, dataclass, replace
from typing import Any, Self

import numpy as np
from numpy.typing import NDArray

from socialmgr.modelling.monitors.charts import Alarm

ACI_ALPHA = 0.2
ACI_GAMMA = 0.05
ACI_BOUNDS = (0.01, 0.6)
ACI_ALARM_LEVEL = 0.10
ACI_ALARM_RUN = 20


def aci_recursion(
    alpha_t: NDArray[np.float64], pit: NDArray[np.float64], alpha: float, gamma: float, lo: float, hi: float
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    """One ACI step on scalars or arrays: (α_{t+1}, err_t)."""
    err = (pit < alpha_t / 2.0) | (pit > 1.0 - alpha_t / 2.0)
    return np.clip(alpha_t + gamma * (alpha - err), lo, hi), err


@dataclass(frozen=True)
class AciState:
    name: str = "aci"
    alpha: float = ACI_ALPHA
    gamma: float = ACI_GAMMA
    alpha_lo: float = ACI_BOUNDS[0]
    alpha_hi: float = ACI_BOUNDS[1]
    alarm_level: float = ACI_ALARM_LEVEL
    alarm_run: int = ACI_ALARM_RUN
    alpha_t: float = ACI_ALPHA
    low_streak: int = 0
    n: int = 0
    misses: int = 0
    alarm_count: int = 0
    last_alarm_index: int | None = None
    armed: bool = False

    @property
    def interval_levels(self) -> tuple[float, float]:
        """Quantile levels of the predictive that form the current ACI interval."""
        return self.alpha_t / 2.0, 1.0 - self.alpha_t / 2.0

    @property
    def in_alarm(self) -> bool:
        return self.low_streak >= self.alarm_run

    def update(self, pit: float, *, armed: bool) -> tuple[Self, Alarm | None]:
        if not armed:
            return replace(self, armed=False), None
        half = self.alpha_t / 2.0
        err = pit < half or pit > 1.0 - half
        alpha_next = min(max(self.alpha_t + self.gamma * (self.alpha - err), self.alpha_lo), self.alpha_hi)
        streak = self.low_streak + 1 if alpha_next < self.alarm_level else 0
        n = self.n + 1
        nxt = replace(
            self, alpha_t=alpha_next, low_streak=streak, n=n, misses=self.misses + int(err), armed=True
        )
        if streak != self.alarm_run:
            return nxt, None
        alarm = Alarm(chart=self.name, index=n, side=None, statistic=alpha_next, threshold=self.alarm_level)
        return replace(nxt, alarm_count=self.alarm_count + 1, last_alarm_index=n), alarm

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)
