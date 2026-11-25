"""Replicated simulations of the experiment statistics (MODEL §6.3, §6.4; IMPLEMENTATION_PLAN §9 M2).

Each function runs many simulated experiments at once through the same code the engine uses:
the Gibbs sampler behind ``analyze``, ``decision_codes`` behind ``decide``, the randomization test,
and ``cs_upper_from_stats`` behind ``arm_harm_bound``.

Data-generating set-up (Simulation F, docs/research/experiment-design-literature.md §4.7 and its
script ``sim_rule.py``): 3 posts a day, assignments blocked within each day (floor/ceil of k/2 by a
fair coin), log outcome = log f + tau·A + e, where the blind forecast log f has variance
R²·S² and e ~ N(0, (1 - R²)·S²), with S = 1.1 and R² = 0.3 (predictive log-sd 0.92).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray

from socialmgr.experiments.posterior import gibbs_t_regression
from socialmgr.experiments.stats import (
    ARM_HARM_RHO,
    ARM_HARM_THRESHOLD,
    DELTA,
    OUTCOMES,
    Observation,
    build_design,
    cs_upper_from_stats,
    decision_codes,
    randomization_test,
)

FloatArray = NDArray[np.float64]


def draw_block_assignments(rng: np.random.Generator, n_rep: int, block_sizes: Sequence[int]) -> FloatArray:
    """Vectorized ``assign_block`` for one factor: (n_rep, sum(block_sizes)) of 0/1."""
    out = []
    for k in block_sizes:
        n_t = k // 2 + (rng.integers(0, 2, n_rep) if k % 2 else 0)
        ranks = np.argsort(np.argsort(rng.random((n_rep, k)), axis=1), axis=1)
        out.append((ranks < np.reshape(n_t, (-1, 1))).astype(float))
    return np.concatenate(out, axis=1)


def look_sizes(weeks: int, posts_per_day: int, maturity_lag_days: int) -> list[int]:
    """Matured posts at each look: interim looks at the first run after each week boundary
    (posts at least ``maturity_lag_days`` old), the final read with every post matured."""
    interim = [posts_per_day * (7 * w - maturity_lag_days) for w in range(1, weeks)]
    return [*interim, posts_per_day * 7 * weeks]


@dataclass(frozen=True)
class RuleResult:
    lift: float
    n_rep: int
    p_adopt: float
    p_reject: float
    p_near_threshold: float
    mean_looks: float

    @property
    def mc_se(self) -> float:
        return math.sqrt(max(self.p_adopt * (1 - self.p_adopt), 1e-12) / self.n_rep)


def simulate_decision_rule(
    lift: float,
    n_rep: int,
    seed: int,
    *,
    weeks: int = 4,
    posts_per_day: int = 3,
    total_log_sd: float = 1.1,
    r2: float = 0.3,
    maturity_lag_days: int = 0,
    carryover: bool = True,
    n_draws: int = 1000,
    burn_in: int = 300,
    t_df: float | None = None,
    day_icc: float = 0.0,
) -> RuleResult:
    """P(adopt) etc. under the full MODEL §6.2 analysis and the §6.3 rule at weekly looks.

    ``maturity_lag_days=0`` is Simulation F's set-up (every post of the elapsed weeks is in the
    look); 2 is batch mode, where posts younger than 48h are not yet mature. Stops at the first
    adopt or reject. ``t_df`` swaps the normal errors for scaled Student-t ones of the same
    variance; ``day_icc`` moves that share of the residual variance into day shocks.
    """
    rng = np.random.default_rng(seed)
    n = posts_per_day * 7 * weeks
    days = 7 * weeks
    arms = draw_block_assignments(rng, n_rep, [posts_per_day] * days)
    log_f = 5.0 + rng.normal(0, total_log_sd * math.sqrt(r2), (n_rep, n))
    resid_sd = total_log_sd * math.sqrt(1 - r2)
    if t_df is not None:
        e = rng.standard_t(t_df, (n_rep, n)) / math.sqrt(t_df / (t_df - 2))
    else:
        e = rng.standard_normal((n_rep, n))
    day_idx = np.repeat(np.arange(days), posts_per_day)
    shocks = rng.standard_normal((n_rep, days))[:, day_idx]
    y = log_f + math.log(lift) * arms + resid_sd * (math.sqrt(1 - day_icc) * e + math.sqrt(day_icc) * shocks)
    prev = np.concatenate([np.zeros((n_rep, 1)), arms[:, :-1]], axis=1)

    outcome = np.zeros(n_rep, dtype=np.intp)  # index into OUTCOMES; 0 = still running
    looks_used = np.zeros(n_rep)
    sizes = look_sizes(weeks, posts_per_day, maturity_lag_days)
    for li, m in enumerate(sizes):
        final = li == len(sizes) - 1
        active = np.flatnonzero(outcome == 0)
        if len(active) == 0:
            break
        design = build_design(
            log_f[active, :m],
            arms[active, :m, None],
            ["A"],
            prev[active, :m, None] if carryover else None,
        )
        res = gibbs_t_regression(
            design.x,
            y[active, :m],
            day_idx[:m],
            design.prior_mean,
            design.prior_prec,
            [design.names.index("tau:A")],
            n_draws=n_draws,
            burn_in=burn_in,
            seed=seed * 1000 + li,
        )
        d = res.draws[:, :, 0]
        lo, hi = np.quantile(d, [0.1, 0.9], axis=1)
        codes = decision_codes(
            np.mean(d > DELTA, axis=1),
            np.mean(d < 0, axis=1),
            np.mean(np.maximum(0.0, -d), axis=1),
            lo,
            hi,
            at_horizon=final,
        )
        outcome[active] = codes
        looks_used[active] += 1
    return RuleResult(
        lift=lift,
        n_rep=n_rep,
        p_adopt=float(np.mean(outcome == OUTCOMES.index("adopt"))),
        p_reject=float(np.mean(outcome == OUTCOMES.index("reject"))),
        p_near_threshold=float(np.mean(outcome == OUTCOMES.index("near_threshold"))),
        mean_looks=float(looks_used.mean()),
    )


def simulate_aa_rejections(
    n_rep: int,
    seed: int,
    *,
    n_draws: int = 999,
    weeks: int = 4,
    posts_per_day: int = 3,
    alpha: float = 0.05,
) -> float:
    """Share of A/A experiments (no effect) in which ``randomization_test`` rejects at ``alpha``.

    Data: Simulation F's set-up made harsher, with t(4) errors and day shocks (ICC 0.3)."""
    rng = np.random.default_rng(seed)
    days = 7 * weeks
    n = days * posts_per_day
    day_idx = np.repeat(np.arange(days), posts_per_day)
    start = date(2026, 1, 5)
    rejections = 0
    for r in range(n_rep):
        arms = draw_block_assignments(rng, 1, [posts_per_day] * days)[0]
        log_f = 5.0 + rng.normal(0, 1.1 * math.sqrt(0.3), n)
        e = rng.standard_t(4, n) / math.sqrt(2)
        shocks = rng.standard_normal(days)[day_idx]
        y = log_f + 1.1 * math.sqrt(0.7) * (math.sqrt(0.7) * e + math.sqrt(0.3) * shocks)
        obs = [
            Observation(
                slot_key=f"{i}",
                block=f"{day_idx[i]}",
                day=start + timedelta(days=int(day_idx[i])),
                log_y=float(y[i]),
                log_f=float(log_f[i]),
                arms={"A": int(arms[i])},
            )
            for i in range(n)
        ]
        rejections += randomization_test(obs, "A", seed=seed * 100_000 + r, n_draws=n_draws) <= alpha
    return rejections / n_rep


@dataclass(frozen=True)
class GuardrailResult:
    trip_rate: float
    n_rep: int
    mean_trip_day: float  # among experiments that tripped


def simulate_arm_harm(
    lift: float,
    n_rep: int,
    seed: int,
    *,
    log_sd: float = 1.0,
    weeks: int = 4,
    posts_per_day: int = 3,
    look_every_days: int = 1,
    maturity_lag_days: int = 2,
    alpha: float = 0.05,
    rho: float = ARM_HARM_RHO,
    t_df: float | None = None,
) -> GuardrailResult:
    """How often the arm-harm guardrail trips (upper CS bound < log 0.8) in one experiment.

    Values are log(y/f) with predictive log-sd ``log_sd`` and a true treated effect log(lift).
    The guardrail is checked at a run every ``look_every_days`` (1: every daily run, 7: weekly)
    using the block contrasts of the days that have fully matured, as ``arm_harm_bound`` does
    with blocks."""
    rng = np.random.default_rng(seed)
    days = 7 * weeks
    arms = draw_block_assignments(rng, n_rep, [posts_per_day] * days).reshape(n_rep, days, posts_per_day)
    if t_df is not None:
        e = rng.standard_t(t_df, arms.shape) / math.sqrt(t_df / (t_df - 2))
    else:
        e = rng.standard_normal(arms.shape)
    v = log_sd * e + math.log(lift) * arms
    n_t = arms.sum(axis=2)
    z = (v * arms).sum(axis=2) / n_t - (v * (1 - arms)).sum(axis=2) / (posts_per_day - n_t)  # (R, days)
    t = np.arange(1, days + 1, dtype=float)
    mean = np.cumsum(z, axis=1) / t
    ss = np.cumsum(z**2, axis=1) - t * mean**2
    upper = cs_upper_from_stats(t, mean, np.maximum(ss, 0.0), alpha, rho)  # (R, days): after b blocks
    # The run on day d sees blocks 0..d-lag-1; final run at horizon + lag sees them all.
    run_days = list(range(look_every_days, days + maturity_lag_days, look_every_days))
    if run_days[-1] != days + maturity_lag_days:
        run_days.append(days + maturity_lag_days)
    seen = sorted({min(d - maturity_lag_days, days) for d in run_days if d - maturity_lag_days >= 1})
    cols = np.array(seen) - 1
    trips = upper[:, cols] < ARM_HARM_THRESHOLD
    tripped = trips.any(axis=1)
    first = np.where(tripped, np.argmax(trips, axis=1), -1)
    trip_days = np.array(seen)[first[tripped]] + maturity_lag_days if tripped.any() else np.array([])
    return GuardrailResult(
        trip_rate=float(tripped.mean()),
        n_rep=n_rep,
        mean_trip_day=float(trip_days.mean()) if len(trip_days) else math.nan,
    )


def old_rule_trip_rate(n_rep: int, seed: int, *, log_sd: float, n_treated: int = 42) -> float:
    """The rule MODEL §6.4 replaced: pause after 3 consecutive treated posts below 0.5x forecast."""
    rng = np.random.default_rng(seed)
    low = (rng.normal(0, log_sd, (n_rep, n_treated)) < math.log(0.5)).astype(int)
    run3 = low[:, :-2] & low[:, 1:-1] & low[:, 2:]
    return float(run3.any(axis=1).mean())


def simulate_elond_fdr(
    n_rep: int,
    seed: int,
    *,
    n_tests: int = 40,
    null_share: float = 0.7,
    effect_z: float = 4.0,
    rho: float = 0.0,
    alpha: float = 0.10,
) -> tuple[float, float]:
    """(FDR, mean rejections) of e-LOND on streams of calibrated e-values e = 1/(2·sqrt(p)).

    One-sided z-tests; nulls have mean 0, alternatives ``effect_z``. ``rho`` > 0 correlates all
    test statistics through a shared factor (dependence e-LOND must survive)."""
    from socialmgr.experiments.stats import ELond, e_value_from_p

    rng = np.random.default_rng(seed)
    fdp = np.zeros(n_rep)
    n_rej = np.zeros(n_rep)
    from scipy.stats import norm

    for r in range(n_rep):
        is_null = rng.random(n_tests) < null_share
        shared = rng.standard_normal()
        zs = (
            math.sqrt(rho) * shared
            + math.sqrt(1 - rho) * rng.standard_normal(n_tests)
            + np.where(is_null, 0, effect_z)
        )
        p = norm.sf(zs)
        el = ELond(alpha=alpha)
        rej = np.array([el.test(e_value_from_p(float(pi))) for pi in p])
        n_rej[r] = rej.sum()
        fdp[r] = (rej & is_null).sum() / max(rej.sum(), 1)
    return float(fdp.mean()), float(n_rej.mean())


__all__ = [
    "GuardrailResult",
    "RuleResult",
    "draw_block_assignments",
    "look_sizes",
    "old_rule_trip_rate",
    "simulate_aa_rejections",
    "simulate_arm_harm",
    "simulate_decision_rule",
    "simulate_elond_fdr",
]
