"""Thin glue between the engine and ``stats``: the effect estimate the mSPRT e-value takes,
block-aware arm harm, and e-LOND state with the stream's stored gamma sequence."""

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from socialmgr.experiments import stats


def ols_effect(obs: Sequence[stats.Observation], factor: str) -> tuple[float, float] | None:
    """Least-squares effect of ``factor`` on log y given [1, log f, every factor's arm], with its
    standard error: the (estimate, se) the mSPRT e-value takes. None without residual df."""
    names = sorted({k for o in obs for k in o.arms})
    if factor not in names:
        return None
    x = np.column_stack(
        [np.ones(len(obs)), [o.log_f for o in obs], *[[float(o.arms.get(k, 0)) for o in obs] for k in names]]
    )
    y = np.array([o.log_y for o in obs])
    dof = len(obs) - x.shape[1]
    if dof < 1 or np.linalg.matrix_rank(x) < x.shape[1]:
        return None
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    sigma2 = float(np.sum((y - x @ coef) ** 2) / dof)
    cov = sigma2 * np.linalg.inv(x.T @ x)
    j = 2 + names.index(factor)
    return float(coef[j]), math.sqrt(max(float(cov[j, j]), 1e-12))


def e_value(obs: Sequence[stats.Observation], factor: str, randomization_p: float) -> tuple[float, str]:
    """The e-value e-LOND gets for a concluded factor and its kind. MODEL §6.7 allows the mSPRT
    likelihood ratio (uncapped) or e = 1/(2√p); the p calibrator caps at 50 with 9,999 draws, which
    can't clear e-LOND's thresholds after the first test, so mSPRT is used whenever available."""
    effect = ols_effect(obs, factor)
    if effect is not None:
        return float(stats.msprt_e_value(effect[0], effect[1])), "msprt"
    # Too few posts for a residual estimate: the calibrator is the only valid e-value left.
    return stats.e_value_from_p(randomization_p), "p_calibrator"


def harm_bound(
    treated: Sequence[float],
    control: Sequence[float],
    treated_blocks: Sequence[str],
    control_blocks: Sequence[str],
) -> float:
    """``arm_harm_bound`` on randomization blocks (valid by design) when stats supports them."""
    return float(
        stats.arm_harm_bound(treated, control, treated_blocks=treated_blocks, control_blocks=control_blocks)
    )


def elond(
    alpha: float, tests: int, rejections: int, history: list[dict[str, Any]], gamma: str | None
) -> stats.ELond:
    """An e-LOND state. ``gamma`` is the stream's stored sequence (None for a new stream, which then
    takes stats' default); the sequence must never change mid-stream."""
    kwargs: dict[str, Any] = {"alpha": alpha, "tests": tests, "rejections": rejections, "history": history}
    if gamma is not None:
        kwargs["gamma_sequence"] = gamma
    return stats.ELond(**kwargs)


def gamma_of(state: stats.ELond) -> str:
    return str(state.gamma_sequence)
