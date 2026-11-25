"""Experiment statistics (MODEL.md §6.1-§6.8). Pure functions: no database, no clock.

This module is the contract between the experiment engine (``experiments/engine.py``) and the
statistics. Signatures are stable; implementations are hardened against MODEL §6's operating
characteristics by the simulation tests in ``tests/test_experiment_stats.py``.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy import stats as sps

DELTA = math.log(1.10)  # MODEL §6.3: the smallest lift worth adopting
ARM_HARM_THRESHOLD = math.log(0.8)  # MODEL §6.4: pause an arm when the CS upper bound falls below
N_RANDOMIZATION_DRAWS = 9_999  # MODEL §6.2


# ---------------------------------------------------------------- assignment


def assign_block(
    slot_keys: Sequence[str], factors: Sequence[str], rng: np.random.Generator
) -> dict[str, dict[str, int]]:
    """Blocked micro-randomization for one block (one local day, or a 2-day pair at 1 post/day).

    For each factor independently, floor(k/2) or ceil(k/2) of the block's k slots are treated
    (π = 0.5; the extra one, when k is odd, goes to a fair coin). Factors are drawn independently,
    which makes up to 3 factors a full factorial on the same posts (MODEL §6.1).
    Returns {slot_key: {factor: 0|1}}.
    """
    k = len(slot_keys)
    out: dict[str, dict[str, int]] = {s: {} for s in slot_keys}
    for factor in factors:
        n_treated = k // 2 + (int(rng.integers(2)) if k % 2 else 0)
        treated = set(rng.choice(k, size=n_treated, replace=False).tolist()) if n_treated else set()
        for i, s in enumerate(slot_keys):
            out[s][factor] = int(i in treated)
    return out


# ---------------------------------------------------------------- analysis


@dataclass(frozen=True)
class Observation:
    """One matured experiment post, intention-to-treat on its original assignment."""

    slot_key: str
    block: str  # the randomization block (local day or 2-day pair) the arms were drawn in
    day: date  # local posting day (day random effect)
    log_y: float  # log(1 + I48 outcome)
    log_f: float  # log(1 + median of the frozen treatment-blind forecast)
    arms: Mapping[str, int]  # factor -> 0/1 as assigned
    prev_arms: Mapping[str, int] | None = None  # the previous experiment post's arms (carryover)


@dataclass(frozen=True)
class FactorPosterior:
    factor: str
    mean: float  # posterior mean of tau (log-ratio effect)
    sd: float
    draws: NDArray[np.float64] = field(repr=False)
    n_treated: int
    n_control: int

    @property
    def ratio(self) -> float:
        return math.exp(self.mean)

    def p_greater(self, x: float) -> float:
        return float(np.mean(self.draws > x))

    def p_less(self, x: float) -> float:
        return float(np.mean(self.draws < x))

    @property
    def expected_loss(self) -> float:
        """E[max(0, -theta)]: what adopting costs if the lever actually hurts."""
        return float(np.mean(np.maximum(0.0, -self.draws)))

    def interval(self, level: float = 0.8) -> tuple[float, float]:
        lo, hi = np.quantile(self.draws, [(1 - level) / 2, (1 + level) / 2])
        return float(lo), float(hi)


def analyze(
    obs: Sequence[Observation], factors: Sequence[str], seed: int, n_draws: int = 4000
) -> dict[str, FactorPosterior]:
    """Posterior for each factor's effect tau_k (MODEL §6.2).

    log y = a + beta·log f + sum_k tau_k·A_k + gamma_k·A_k·(log f - mean) + lambda_k·A_prev,k + e

    Reference implementation: Bayesian linear regression, normal errors, conjugate normal prior on
    the coefficients (tau_k ~ N(0, 0.2²) around μ_fam = 0, beta ~ N(1, 0.25²), wide elsewhere) with
    sigma at its residual estimate. TODO(stats stream): Student-t errors, day random effect and the
    hierarchical μ_fam per MODEL §6.2, keeping this signature.
    """
    n = len(obs)
    if n == 0:
        raise ValueError("no observations")
    log_f = np.array([o.log_f for o in obs])
    lf_c = log_f - log_f.mean()
    cols = [np.ones(n), log_f]
    names = ["a", "beta"]
    prior_mean = [0.0, 1.0]
    prior_sd = [10.0, 0.25]
    for k in factors:
        a_k = np.array([float(o.arms.get(k, 0)) for o in obs])
        cols += [a_k, a_k * lf_c]
        names += [f"tau:{k}", f"gamma:{k}"]
        prior_mean += [0.0, 0.0]
        prior_sd += [0.2, 0.5]
        if all(o.prev_arms is not None for o in obs):
            prev = np.array([float((o.prev_arms or {}).get(k, 0)) for o in obs])
            cols.append(prev)
            names.append(f"lambda:{k}")
            prior_mean.append(0.0)
            prior_sd.append(0.5)
    x = np.column_stack(cols)
    y = np.array([o.log_y for o in obs])
    p0 = np.diag(1.0 / np.square(prior_sd))
    m0 = np.array(prior_mean)
    # sigma: residual sd of a least-squares fit, floored (MODEL: sigma ~ half-N(1.5)).
    coef_ls, *_ = np.linalg.lstsq(x, y, rcond=None)
    dof = max(n - x.shape[1], 1)
    sigma = max(float(np.sqrt(np.sum((y - x @ coef_ls) ** 2) / dof)), 0.3)
    precision = p0 + x.T @ x / sigma**2
    cov = np.linalg.inv(precision)
    mean = cov @ (p0 @ m0 + x.T @ y / sigma**2)
    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal(mean, cov, size=n_draws, method="cholesky")
    out = {}
    for k in factors:
        j = names.index(f"tau:{k}")
        assigned = [o.arms.get(k, 0) for o in obs]
        out[k] = FactorPosterior(
            factor=k,
            mean=float(mean[j]),
            sd=float(math.sqrt(cov[j, j])),
            draws=draws[:, j].copy(),
            n_treated=int(sum(assigned)),
            n_control=int(n - sum(assigned)),
        )
    return out


# ---------------------------------------------------------------- decisions


Outcome = Literal["adopt", "reject", "near_threshold", "continue"]


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    p_gt_delta: float
    p_lt_zero: float
    expected_loss: float


def decide(post: FactorPosterior, at_horizon: bool, delta: float = DELTA) -> Decision:
    """MODEL §6.3 decision rule. Evaluated only at the first run after each experiment-week
    boundary and at the final read (horizon + 48h)."""
    p_gt = post.p_greater(delta)
    p_lt0 = post.p_less(0.0)
    loss = post.expected_loss
    if p_gt >= 0.80 and loss < 0.01:
        return Decision(
            "adopt",
            f"P(lift > {math.exp(delta):.2f}x) = {p_gt:.2f}, expected loss {loss:.3f}",
            p_gt,
            p_lt0,
            loss,
        )
    if p_lt0 >= 0.90:
        return Decision("reject", f"P(effect < 0) = {p_lt0:.2f}", p_gt, p_lt0, loss)
    if at_horizon:
        lo, hi = post.interval(0.8)
        if p_gt < 0.20:
            return Decision(
                "reject", f"P(lift > {math.exp(delta):.2f}x) = {p_gt:.2f} at the horizon", p_gt, p_lt0, loss
            )
        if lo < delta < hi:
            return Decision(
                "near_threshold",
                "80% interval straddles δ at the horizon: one extension of up to 50% is allowed",
                p_gt,
                p_lt0,
                loss,
            )
    return Decision("continue", "no decision yet", p_gt, p_lt0, loss)


def randomization_test(
    obs: Sequence[Observation],
    factor: str,
    seed: int,
    n_draws: int = N_RANDOMIZATION_DRAWS,
) -> float:
    """Two-sided randomization p-value for one factor (MODEL §6.2), re-drawing the same blocked
    design: within each block the number treated is kept and the treated set is permuted.
    Statistic: difference in mean residual log(y) - log(f) between arms."""
    resid = np.array([o.log_y - o.log_f for o in obs])
    arms = np.array([o.arms.get(factor, 0) for o in obs])
    blocks: dict[str, list[int]] = {}
    for i, o in enumerate(obs):
        blocks.setdefault(o.block, []).append(i)

    def stat(a: NDArray[Any]) -> float:
        if a.sum() == 0 or a.sum() == len(a):
            return 0.0
        return float(resid[a == 1].mean() - resid[a == 0].mean())

    observed = abs(stat(arms))
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_draws):
        perm = arms.copy()
        for idx in blocks.values():
            perm[idx] = rng.permutation(arms[idx])
        hits += abs(stat(perm)) >= observed - 1e-12
    return (hits + 1) / (n_draws + 1)


# ---------------------------------------------------------------- multiplicity (MODEL §6.7)


def e_value_from_p(p: float) -> float:
    """Calibrator e = 1/(2·sqrt(p)) (MODEL §6.7), capped for p -> 0."""
    return 1.0 / (2.0 * math.sqrt(max(p, 1e-12)))


@dataclass
class ELond:
    """e-LOND over the whole stream of concluded confirmatory experiments, FDR alpha (default 0.10).

    Test t (1-based) rejects when e_t >= 1 / alpha_t with alpha_t = alpha · gamma_t · (R_{t-1} + 1),
    gamma_t = 6 / (pi² t²) (sums to 1). Valid under arbitrary dependence (Xu & Ramdas).
    Only e-LOND rejections may be labelled "confirmed".
    """

    alpha: float = 0.10
    tests: int = 0
    rejections: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def gamma(self, t: int) -> float:
        return 6.0 / (math.pi**2 * t**2)

    def test(self, e: float, label: str = "") -> bool:
        self.tests += 1
        level = self.alpha * self.gamma(self.tests) * (self.rejections + 1)
        rejected = e >= 1.0 / level
        self.rejections += int(rejected)
        self.history.append({"t": self.tests, "label": label, "e": e, "alpha_t": level, "rejected": rejected})
        return rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "tests": self.tests,
            "rejections": self.rejections,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ELond":
        return cls(alpha=d["alpha"], tests=d["tests"], rejections=d["rejections"], history=list(d["history"]))


# ---------------------------------------------------------------- guardrails (MODEL §6.4)


def cs_upper_bound(values: Sequence[float], alpha: float = 0.05, rho: float = 1.0) -> float:
    """One-sided normal-mixture confidence sequence (Howard et al.) for a mean: an upper bound
    valid at every look. Variance from the data, floored. Returns +inf with fewer than 3 values."""
    x = np.asarray(values, dtype=float)
    t = len(x)
    if t < 3:
        return math.inf
    var = max(float(np.var(x, ddof=1)), 0.25)
    v = t * var
    radius = math.sqrt((v + rho) * (math.log((v + rho) / rho) + 2 * math.log(1 / alpha))) / t
    return float(x.mean() + radius)


def arm_harm_bound(treated: Sequence[float], control: Sequence[float], alpha: float = 0.05) -> float:
    """Upper confidence-sequence bound on mean(treated) - mean(control) of log(y/f).
    The arm pauses when this falls below ``ARM_HARM_THRESHOLD`` (log 0.8)."""
    if len(treated) < 3 or len(control) < 3:
        return math.inf
    ut = cs_upper_bound(treated, alpha / 2)
    lc = -cs_upper_bound([-v for v in control], alpha / 2)  # lower bound on the control mean
    return ut - lc


# ---------------------------------------------------------------- power (MODEL §6.8)


@dataclass(frozen=True)
class PowerPreview:
    n_posts: int
    power: dict[str, float]  # "1.25x", "1.5x", "2x"
    mde_ratio_80: float


def power_preview(n_posts: int, sigma: float = 1.1, r2: float = 0.3, alpha: float = 0.05) -> PowerPreview:
    """The three-number preview: power at 1.25x / 1.5x / 2x and the MDE at 80% power, from
    SE = 2·sigma·sqrt(1 - R²)/sqrt(n) (MODEL §6.8; matches simulation within ±0.03)."""
    se = 2 * sigma * math.sqrt(1 - r2) / math.sqrt(max(n_posts, 1))
    z = sps.norm.ppf(1 - alpha / 2)

    def pw(lift: float) -> float:
        d = math.log(lift) / se
        return float(sps.norm.cdf(d - z) + sps.norm.cdf(-d - z))

    mde = math.exp((z + sps.norm.ppf(0.8)) * se)
    return PowerPreview(
        n_posts,
        {"1.25x": round(pw(1.25), 2), "1.5x": round(pw(1.5), 2), "2x": round(pw(2.0), 2)},
        round(mde, 2),
    )
