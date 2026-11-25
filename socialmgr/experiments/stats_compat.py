"""Adapters between the engine and stats helpers that land with the stats stream
(branch worktree-agent-abfaeb96ed7667548, commit 542c16b): ``msprt_e_value``, block-aware
``arm_harm_bound`` and ``ELond.gamma_sequence``.

TODO(merge): once that branch is merged, ``stats`` has all three and the fallbacks below are dead
code; replace these adapters with direct calls. No stats code is copied here.
"""

import dataclasses
import inspect
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from socialmgr.experiments import stats

LEGACY_GAMMA = "6/(pi^2 t^2)"  # the only sequence the pre-merge ELond knows


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
    msprt = getattr(stats, "msprt_e_value", None)
    effect = ols_effect(obs, factor)
    if msprt is not None and effect is not None:
        return float(msprt(effect[0], effect[1])), "msprt"
    # TODO(merge): pre-merge fallback; stats.msprt_e_value arrives with commit 542c16b.
    return stats.e_value_from_p(randomization_p), "p_calibrator"


def harm_bound(
    treated: Sequence[float],
    control: Sequence[float],
    treated_blocks: Sequence[str],
    control_blocks: Sequence[str],
) -> float:
    """``arm_harm_bound`` on randomization blocks (valid by design) when stats supports them."""
    if "treated_blocks" in inspect.signature(stats.arm_harm_bound).parameters:
        fn: Any = stats.arm_harm_bound
        return float(fn(treated, control, treated_blocks=treated_blocks, control_blocks=control_blocks))
    # TODO(merge): pre-merge fallback without blocks.
    return stats.arm_harm_bound(treated, control)


def _has_gamma_field() -> bool:
    return "gamma_sequence" in {f.name for f in dataclasses.fields(stats.ELond)}


def elond(
    alpha: float, tests: int, rejections: int, history: list[dict[str, Any]], gamma: str | None
) -> stats.ELond:
    """An e-LOND state. ``gamma`` is the stream's stored sequence (None for a new stream, which then
    takes stats' default); the sequence must never change mid-stream."""
    kwargs: dict[str, Any] = {"alpha": alpha, "tests": tests, "rejections": rejections, "history": history}
    if _has_gamma_field():
        if gamma is not None:
            kwargs["gamma_sequence"] = gamma
    elif gamma not in (None, LEGACY_GAMMA):
        raise RuntimeError(f"this e-LOND stream uses gamma {gamma!r}, which this stats version lacks")
    return stats.ELond(**kwargs)


def gamma_of(state: stats.ELond) -> str:
    return str(getattr(state, "gamma_sequence", LEGACY_GAMMA))
