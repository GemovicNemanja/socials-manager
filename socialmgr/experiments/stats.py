"""Experiment statistics (MODEL.md §6.1-§6.8). Pure functions: no database, no clock.

This module is the contract between the experiment engine (``experiments/engine.py``) and the
statistics. Signatures are stable; implementations are checked against MODEL §6's operating
characteristics by the simulation tests in ``tests/test_experiment_stats.py`` (simulation code in
``experiments/simulate.py``).

Choices and deviations from MODEL §6, in one place:

- ``analyze`` fits the full §6.2 model by Gibbs sampling (``experiments/posterior.py``). The
  family prior is parametrized so that each tau_k's marginal prior sd is s0 = 0.2, the value §6.3's
  decision rule and Simulation F were calibrated with: tau_k = mu_fam + eta_k with
  var(mu_fam) = s0² - s_fam² and eta_k ~ N(0, s_fam²). MODEL writes mu_fam ~ N(0, 0.2²) literally,
  which with s_fam > 0 would make the marginal prior 0.22 and loosen the rule. s_fam (0.1) is
  fixed, not estimated: with at most 3 factors per experiment it is not identified. The day
  random effect's sd, which MODEL leaves open, gets a half-N(0.5) prior.
- ``randomization_test`` uses a conditional randomization test (each block keeps its observed
  number treated) on regression-residualized outcomes.
- The guardrail confidence sequence is a self-normalized normal-mixture CS (Howard et al. 2021,
  with de la Peña's inequality for conditionally symmetric increments). It needs no variance
  assumption; with randomization blocks it is valid by design alone.
- e-LOND uses gamma_t = 1/(t(t+1)), the sequence of Xu & Ramdas's own simulations. With the
  p calibrator and a 9,999-draw randomization p (so e <= 50), only the first test of a stream
  without earlier rejections can reach the 1/alpha_t threshold (20 at t = 1, 60 at t = 2);
  ``msprt_e_value`` gives an uncapped e-value (MODEL §6.7 allows either).
- ``power_preview`` computes power against a noncentral t (n - 4 df) instead of the normal: the
  normal reference misses the §6.8 table's simulated cells by up to 0.05 at n = 28-42.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy import stats as sps
from scipy.special import log_ndtr

from socialmgr.experiments.posterior import gibbs_t_regression

DELTA = math.log(1.10)  # MODEL §6.3: the smallest lift worth adopting
ARM_HARM_THRESHOLD = math.log(0.8)  # MODEL §6.4: pause an arm when the CS upper bound falls below
N_RANDOMIZATION_DRAWS = 9_999  # MODEL §6.2
S0 = 0.2  # MODEL §6.3: skeptical prior sd of each tau_k (log scale)
S_FAM = 0.1  # spread of experiments around their family mean (fixed; see module docstring)
ARM_HARM_RHO = 8.0  # normal-mixture tuning: tightest near V ≈ 64, a 4-week experiment's sum of squares

FloatArray = NDArray[np.float64]


# ---------------------------------------------------------------- assignment


def assign_block(
    slot_keys: Sequence[str], factors: Sequence[str], rng: np.random.Generator
) -> dict[str, dict[str, int]]:
    """Blocked micro-randomization for one block (one local day, or a 2-day pair at 1 post/day).

    For each factor independently, floor(k/2) or ceil(k/2) of the block's k slots are treated
    (π = 0.5; the extra one, when k is odd, goes to a fair coin). Factors are drawn independently,
    which makes up to 3 factors a full factorial on the same posts (MODEL §6.1).
    Returns {slot_key: {factor: 0|1}}.

    The design is invariant under swapping treated and control, which is what makes the
    block contrasts in ``arm_harm_bound`` symmetric by design.
    """
    if len(set(slot_keys)) != len(slot_keys):
        raise ValueError("slot keys in a block must be unique")
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
    slot: str | None = None  # time-of-day slot label (alpha_slot); None drops the slot effects


@dataclass(frozen=True)
class FactorPosterior:
    factor: str
    mean: float  # posterior mean of tau (log-ratio effect at the mean blind forecast)
    sd: float
    draws: NDArray[np.float64] = field(repr=False)
    n_treated: int
    n_control: int
    carryover_mean: float | None = None  # lambda_k, when prev_arms were given (MODEL §6.6)
    carryover_sd: float | None = None

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


@dataclass(frozen=True)
class Design:
    """Design matrix and Gaussian prior for the coefficients of the MODEL §6.2 model."""

    x: FloatArray  # (R, n, p)
    names: list[str]
    prior_mean: FloatArray  # (p,)
    prior_prec: FloatArray  # (p, p)


def tau_prior_cov(
    factors: Sequence[str],
    s0: float = S0,
    s_fam: float = S_FAM,
    families: Mapping[str, str] | None = None,
    family_priors: Mapping[str, tuple[float, float]] | None = None,
) -> tuple[FloatArray, FloatArray]:
    """Joint prior of (tau_k) with mu_fam integrated out: tau_k = mu_fam(k) + eta_k.

    A family with no history has mu_fam ~ N(0, s0² - s_fam²), so each tau_k is marginally
    N(0, s0²). ``family_priors`` maps a family to (mean, sd) of mu_fam learned from concluded
    experiments. Factors in the same family share mu_fam (covariance var(mu_fam)).
    Returns (mean, covariance).
    """
    if s_fam > s0:
        raise ValueError("s_fam must not exceed s0")
    fam = [families.get(k, k) if families else k for k in factors]
    default = (0.0, math.sqrt(s0**2 - s_fam**2))
    mu = [(family_priors or {}).get(f, default) for f in fam]
    mean = np.array([m for m, _ in mu])
    cov = np.array(
        [[mu[i][1] ** 2 if fam[i] == fam[j] else 0.0 for j in range(len(fam))] for i in range(len(fam))]
    )
    cov = cov + s_fam**2 * np.eye(len(fam))
    return mean, cov


def build_design(
    log_f: FloatArray,
    arms: FloatArray,
    factors: Sequence[str],
    prev_arms: FloatArray | None = None,
    slot_idx: NDArray[np.intp] | None = None,
    *,
    s0: float = S0,
    s_fam: float = S_FAM,
    families: Mapping[str, str] | None = None,
    family_priors: Mapping[str, tuple[float, float]] | None = None,
) -> Design:
    """Columns: intercept, beta (centred log f), slot effects, then per factor tau_k, gamma_k
    (Lin interaction with centred log f) and lambda_k (previous post's arm).

    log_f: (R, n); arms and prev_arms: (R, n, K); slot_idx: (n,) slot index per post.
    Priors: intercept N(mean log f, 5²) (a = 0 when the forecast is calibrated), beta N(1, 0.25²),
    slots N(0, 1²), tau from ``tau_prior_cov``, gamma N(0, 0.5²), lambda N(0, 0.2²).
    """
    r_, n = log_f.shape
    kk = len(factors)
    lf_mean = log_f.mean(axis=1, keepdims=True)
    lf_c = log_f - lf_mean
    cols: list[FloatArray] = [np.ones((r_, n)), lf_c]
    names = ["a", "beta"]
    means: list[float] = [float(lf_mean.mean()), 1.0]
    sds: list[float] = [5.0, 0.25]
    if slot_idx is not None and len(np.unique(slot_idx)) > 1:
        for s in np.unique(slot_idx)[1:]:
            cols.append(np.broadcast_to((slot_idx == s).astype(float), (r_, n)))
            names.append(f"slot:{int(s)}")
            means.append(0.0)
            sds.append(1.0)
    first_tau = len(cols)
    for j, k in enumerate(factors):
        cols.append(arms[:, :, j])
        names.append(f"tau:{k}")
        means.append(0.0)
        sds.append(1.0)  # replaced by the joint tau prior below
    for j, k in enumerate(factors):
        cols.append(arms[:, :, j] * lf_c)
        names.append(f"gamma:{k}")
        means.append(0.0)
        sds.append(0.5)
    if prev_arms is not None:
        for j, k in enumerate(factors):
            cols.append(prev_arms[:, :, j])
            names.append(f"lambda:{k}")
            means.append(0.0)
            sds.append(0.2)
    x = np.stack(cols, axis=-1).astype(float)
    prior_mean = np.array(means)
    prec = np.diag(1.0 / np.square(sds))
    t_mean, t_cov = tau_prior_cov(factors, s0, s_fam, families, family_priors)
    sl = slice(first_tau, first_tau + kk)
    prec[sl, sl] = np.linalg.inv(t_cov)
    prior_mean[sl] = t_mean
    return Design(x, names, prior_mean, prec)


def analyze(
    obs: Sequence[Observation],
    factors: Sequence[str],
    seed: int,
    n_draws: int = 4000,
    *,
    burn_in: int = 500,
    s0: float = S0,
    s_fam: float = S_FAM,
    families: Mapping[str, str] | None = None,
    family_priors: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, FactorPosterior]:
    """Posterior for each factor's effect tau_k (MODEL §6.2), by Gibbs sampling.

    log y = a_slot + beta·(log f - mean) + sum_k [tau_k·A_k + gamma_k·A_k·(log f - mean)
            + lambda_k·A_prev,k] + u_day + e,   e ~ t(nu, 0, sigma)

    tau_k is the effect at the mean blind forecast. lambda_k is included when any observation
    carries ``prev_arms`` (a missing previous post counts as control). See the module docstring
    for the family prior and ``posterior.py`` for the sampler. Reproducible for a given seed;
    about 0.3-0.6 s for n = 20-200 at the default 4,000 draws.
    """
    n = len(obs)
    if n == 0:
        raise ValueError("no observations")
    log_f = np.array([[o.log_f for o in obs]])
    log_y = np.array([[o.log_y for o in obs]])
    arms = np.array([[[float(o.arms.get(k, 0)) for k in factors] for o in obs]])
    prev = None
    if any(o.prev_arms is not None for o in obs):
        prev = np.array([[[float((o.prev_arms or {}).get(k, 0)) for k in factors] for o in obs]])
    slot_idx = None
    if any(o.slot is not None for o in obs):
        labels = sorted({o.slot or "" for o in obs})
        slot_idx = np.array([labels.index(o.slot or "") for o in obs], dtype=np.intp)
    days = sorted({o.day for o in obs})
    day_idx = np.array([days.index(o.day) for o in obs], dtype=np.intp)
    design = build_design(
        log_f,
        arms,
        factors,
        prev,
        slot_idx,
        s0=s0,
        s_fam=s_fam,
        families=families,
        family_priors=family_priors,
    )
    tau_cols = [design.names.index(f"tau:{k}") for k in factors]
    lam_cols = [design.names.index(f"lambda:{k}") for k in factors] if prev is not None else []
    res = gibbs_t_regression(
        design.x,
        log_y,
        day_idx,
        design.prior_mean,
        design.prior_prec,
        tau_cols + lam_cols,
        n_draws=n_draws,
        burn_in=burn_in,
        seed=seed,
    )
    out = {}
    for j, k in enumerate(factors):
        d = res.draws[0, :, j].copy()
        assigned = int(arms[0, :, j].sum())
        lam = res.draws[0, :, len(factors) + j] if prev is not None else None
        out[k] = FactorPosterior(
            factor=k,
            mean=float(d.mean()),
            sd=float(d.std(ddof=1)),
            draws=d,
            n_treated=assigned,
            n_control=n - assigned,
            carryover_mean=float(lam.mean()) if lam is not None else None,
            carryover_sd=float(lam.std(ddof=1)) if lam is not None else None,
        )
    return out


# ---------------------------------------------------------------- decisions


Outcome = Literal["adopt", "reject", "near_threshold", "continue"]
OUTCOMES: tuple[Outcome, ...] = ("continue", "adopt", "reject", "near_threshold")


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    p_gt_delta: float
    p_lt_zero: float
    expected_loss: float


def decision_codes(
    p_gt: FloatArray,
    p_lt0: FloatArray,
    loss: FloatArray,
    lo: FloatArray,
    hi: FloatArray,
    at_horizon: bool,
    delta: float = DELTA,
    extended: bool = False,
) -> NDArray[np.intp]:
    """The MODEL §6.3 rule on arrays: indexes into ``OUTCOMES`` (0 continue, 1 adopt, 2 reject,
    3 near threshold). ``decide`` and the simulations both go through this one function.

    In order: adopt if P(theta > delta) >= 0.80 and E[max(0, -theta)] < 0.01; reject if
    P(theta < 0) >= 0.90. At the horizon only: reject if P(theta > delta) < 0.20; near threshold
    if the 80% interval (lo, hi) straddles delta, unless the experiment was already extended once
    (then reject: one extension only); anything else at the horizon is not adopted (reject).
    """
    adopt = (p_gt >= 0.80) & (loss < 0.01)
    reject = p_lt0 >= 0.90
    conds = [adopt, reject]
    choices = [1, 2]
    if at_horizon:
        straddle = (lo < delta) & (delta < hi)
        conds += [p_gt < 0.20, straddle & (not extended), np.ones_like(adopt)]
        choices += [2, 3, 2]
    return np.select(conds, choices, default=0).astype(np.intp)


def decide(
    post: FactorPosterior, at_horizon: bool, delta: float = DELTA, *, extended: bool = False
) -> Decision:
    """MODEL §6.3 decision rule, evaluated only at a look (see ``decision_looks``): the first run
    after each experiment-week boundary, and the final read at the first run after the horizon
    plus 48h (``at_horizon=True``). ``extended`` marks the read at the end of the one allowed
    extension, where "near threshold" is no longer offered."""
    p_gt = post.p_greater(delta)
    p_lt0 = post.p_less(0.0)
    loss = post.expected_loss
    lo, hi = post.interval(0.8)
    code = int(
        decision_codes(
            np.array([p_gt]),
            np.array([p_lt0]),
            np.array([loss]),
            np.array([lo]),
            np.array([hi]),
            at_horizon,
            delta,
            extended,
        )[0]
    )
    outcome = OUTCOMES[code]
    lift = f"{math.exp(delta):.2f}x"
    if outcome == "adopt":
        reason = f"P(lift > {lift}) = {p_gt:.2f}, expected loss {loss:.3f}"
    elif outcome == "reject" and p_lt0 >= 0.90:
        reason = f"P(effect < 0) = {p_lt0:.2f}"
    elif outcome == "reject" and p_gt < 0.20:
        reason = f"P(lift > {lift}) = {p_gt:.2f} at the horizon"
    elif outcome == "reject" and extended and lo < delta < hi:
        reason = "80% interval still straddles δ after the one allowed extension: not adopted"
    elif outcome == "reject":
        reason = f"not adopted at the horizon (P(lift > {lift}) = {p_gt:.2f}, expected loss {loss:.3f})"
    elif outcome == "near_threshold":
        reason = "80% interval straddles δ at the horizon: one extension of up to 50% is allowed"
    else:
        reason = "no decision yet"
    return Decision(outcome, reason, p_gt, p_lt0, loss)


@dataclass(frozen=True)
class Look:
    run_at: datetime
    week: int  # the experiment week whose boundary this look follows (the final look: horizon weeks)
    final: bool


def decision_looks(
    start: datetime,
    horizon_days: int,
    runs: Sequence[datetime],
    maturity: timedelta = timedelta(hours=48),
) -> list[Look]:
    """Which runs are decision looks (MODEL §6.3, batch mode).

    Interim looks: the first run at or after each experiment-week boundary start + 7w days that
    falls before the horizon (w = 1, 2, ...; a run after several boundaries is one look). The
    final look: the first run at or after start + horizon + ``maturity``. A 4-week horizon gives
    at most 3 interim looks and the final read, the 4 looks of Simulation F; the final read
    replaces a look at the week-4 boundary, when the last two days are not yet mature.
    """
    if start.tzinfo is None or any(r.tzinfo is None for r in runs):
        raise ValueError("timestamps must be timezone-aware")
    ordered = sorted(runs)
    final_at = start + timedelta(days=horizon_days) + maturity
    looks: list[Look] = []
    boundaries = [start + timedelta(days=7 * w) for w in range(1, (horizon_days - 1) // 7 + 1)]
    for run in ordered:
        if run >= final_at:
            looks.append(Look(run, math.ceil(horizon_days / 7), True))
            break
        passed = [w + 1 for w, b in enumerate(boundaries) if run >= b]
        if passed and (not looks or passed[-1] > looks[-1].week):
            looks.append(Look(run, passed[-1], False))
    return looks


def randomization_test(
    obs: Sequence[Observation],
    factor: str,
    seed: int,
    n_draws: int = N_RANDOMIZATION_DRAWS,
) -> float:
    """Two-sided randomization p-value for one factor (MODEL §6.2; Rosenbaum 2002).

    Re-draws the same blocked design conditionally on each block's number treated (an ancillary
    statistic, so the test is exact). Statistic: residualize log y on [1, log f, the other
    factors' arms] by least squares (treatment-blind, so fixed across re-draws), centre the
    residuals within each block, and take sum_i A_i·r_i. With block counts fixed this equals the
    treated-minus-control mean residual up to a constant, centred at its randomization mean.
    p = (1 + #{|T*| >= |T|}) / (1 + n_draws).
    """
    n = len(obs)
    if n == 0:
        raise ValueError("no observations")
    others = sorted({k for o in obs for k in o.arms if k != factor})
    cols = [np.ones(n), np.array([o.log_f for o in obs])]
    cols += [np.array([float(o.arms.get(k, 0)) for o in obs]) for k in others]
    xr = np.column_stack(cols)
    y = np.array([o.log_y for o in obs])
    coef, *_ = np.linalg.lstsq(xr, y, rcond=None)
    resid = y - xr @ coef
    arms = np.array([float(o.arms.get(factor, 0)) for o in obs])
    blocks: dict[str, list[int]] = {}
    for i, o in enumerate(obs):
        blocks.setdefault(o.block, []).append(i)
    rc = resid.copy()
    for members in blocks.values():
        rc[members] -= rc[members].mean()
    observed = abs(float(arms @ rc))
    rng = np.random.default_rng(seed)
    t = np.zeros(n_draws)
    for idx_list in blocks.values():
        idx = np.array(idx_list)
        a_b = arms[idx]
        if a_b.min() == a_b.max():
            continue
        perm = np.argsort(rng.random((n_draws, len(idx))), axis=1)
        t += a_b[perm] @ rc[idx]
    hits = int(np.sum(np.abs(t) >= observed - 1e-12))
    return (hits + 1) / (n_draws + 1)


# ---------------------------------------------------------------- multiplicity (MODEL §6.7)


def e_value_from_p(p: float) -> float:
    """Calibrator e = 1/(2·sqrt(p)) (MODEL §6.7; Vovk & Wang 2021, kappa = 1/2), capped for p -> 0.

    A randomization p-value from 9,999 draws is at least 1e-4, so e <= 50."""
    return 1.0 / (2.0 * math.sqrt(max(p, 1e-12)))


def msprt_e_value(estimate: float, se: float, mix_sd: float = math.log(1.5)) -> float:
    """The other e-value MODEL §6.7 allows: the normal-mixture mSPRT likelihood ratio for
    H0: tau = 0, with tau ~ N(0, mix_sd²) under the alternative (MODEL §6.4: sd log 1.5).

    e = sqrt(se²/(se² + m²))·exp(est²·m²/(2·se²·(se² + m²))). It is a martingale in the data under
    H0 (known se), so it stays an e-value at any stopping time. Unlike the p calibrator it is not
    capped by the number of randomization draws."""
    v, m2 = se**2, mix_sd**2
    return math.sqrt(v / (v + m2)) * math.exp(estimate**2 * m2 / (2 * v * (v + m2)))


GammaSequence = Literal["1/(t(t+1))", "6/(pi^2 t^2)"]


def _gamma(kind: str, t: int) -> float:
    if kind == "1/(t(t+1))":
        return 1.0 / (t * (t + 1))
    if kind == "6/(pi^2 t^2)":
        return 6.0 / (math.pi**2 * t**2)
    raise ValueError(f"unknown gamma sequence {kind!r}")


@dataclass
class ELond:
    """e-LOND over the whole stream of concluded confirmatory experiments, FDR alpha (default 0.10).

    Test t (1-based) rejects when e_t >= 1 / alpha_t with alpha_t = alpha · gamma_t · (R_{t-1} + 1),
    R_{t-1} the number of rejections before t (Xu & Ramdas 2024, "Online multiple testing with
    e-values", AISTATS; arXiv 2311.06412). The gamma_t are fixed in advance, non-negative and sum
    to at most 1; FDR <= alpha at every t under arbitrary dependence, needing only E[e_t] <= 1
    for true nulls. Default gamma_t = 1/(t(t+1)) (the paper's simulations); streams saved before
    this field existed used 6/(pi² t²) and keep it, since the sequence must not change mid-stream.
    Only e-LOND rejections may be labelled "confirmed".
    """

    alpha: float = 0.10
    tests: int = 0
    rejections: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    gamma_sequence: GammaSequence = "1/(t(t+1))"

    def gamma(self, t: int) -> float:
        return _gamma(self.gamma_sequence, t)

    def level(self) -> float:
        """alpha_t for the next test."""
        return self.alpha * self.gamma(self.tests + 1) * (self.rejections + 1)

    def test(self, e: float, label: str = "") -> bool:
        level = self.level()
        self.tests += 1
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
            "gamma_sequence": self.gamma_sequence,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ELond":
        return cls(
            alpha=d["alpha"],
            tests=d["tests"],
            rejections=d["rejections"],
            history=list(d["history"]),
            gamma_sequence=d.get("gamma_sequence", "6/(pi^2 t^2)"),
        )


# ---------------------------------------------------------------- guardrails (MODEL §6.4)
#
# One-sided normal-mixture confidence sequence, self-normalized. If x_1, x_2, ... are conditionally
# symmetric about mu given the past, exp(lam·S_t - lam²·V_t/2) with S_t = sum(mu - x_i) and
# V_t = sum(x_i - mu)² is a non-negative supermartingale for every lam (de la Peña 1999;
# Howard et al. 2021, Table 3). Mixing lam over a half-normal with precision rho gives
#     M_t = 2·sqrt(rho/(V+rho))·exp(S²/(2(V+rho)))·Phi(S/sqrt(V+rho)),
# and by Ville's inequality P(sup_t M_t >= 1/alpha) <= alpha. A value mu is ruled out (as too
# large) once M_t(mu) >= 1/alpha; the upper bound is the largest value not ruled out, so it is
# valid simultaneously at every look, however often the guardrail is checked.

_U_TABLE = np.linspace(0.0, 60.0, 60_001)
_C_TABLE = _U_TABLE**2 / 2 + log_ndtr(_U_TABLE)


def _mixture_boundary(v: FloatArray, alpha: float, rho: float) -> FloatArray:
    """Smallest S with M_t(S, V) >= 1/alpha, for the one-sided (half-normal) mixture."""
    c = math.log(1 / (2 * alpha)) + 0.5 * np.log((v + rho) / rho)
    u = np.interp(c, _C_TABLE, _U_TABLE)
    return np.asarray(u * np.sqrt(v + rho), dtype=float)


def cs_upper_from_stats(
    t: FloatArray | float, mean: FloatArray | float, ss: FloatArray | float, alpha: float, rho: float
) -> FloatArray:
    """Upper confidence-sequence bound from (count, mean, sum of squared deviations), vectorized.

    For mu = mean + d, S_t = t·d and V_t = ss + t·d²; d is searched on a grid out to 20 scale
    units (effects beyond that are treated as impossible), then refined by bisection. +inf when
    nothing within that range is ruled out.
    """
    t_a, m_a, ss_a = np.broadcast_arrays(np.asarray(t, float), np.asarray(mean, float), np.asarray(ss, float))
    scale = np.sqrt((ss_a + rho) / np.maximum(t_a, 1.0))
    q = np.linspace(0.0, 1.0, 257) ** 2 * 20.0
    d = scale[..., None] * q  # (..., G)

    def h(dd: FloatArray, tt: FloatArray, ssx: FloatArray) -> FloatArray:
        return np.asarray(tt * dd - _mixture_boundary(ssx + tt * dd**2, alpha, rho), dtype=float)

    ruled_out = h(d, t_a[..., None], ss_a[..., None]) >= 0
    # last grid point not ruled out (index 0, the mean itself, never is)
    last_ok = d.shape[-1] - 1 - np.argmax(~ruled_out[..., ::-1], axis=-1)
    unbounded = last_ok == d.shape[-1] - 1
    j = np.minimum(last_ok, d.shape[-1] - 2)
    lo = np.take_along_axis(d, j[..., None], -1)[..., 0]
    hi = np.take_along_axis(d, j[..., None] + 1, -1)[..., 0]
    for _ in range(30):
        mid = (lo + hi) / 2
        out = h(mid, t_a, ss_a) >= 0
        hi = np.where(out, mid, hi)
        lo = np.where(out, lo, mid)
    return np.asarray(np.where(unbounded | (t_a < 3), np.inf, m_a + hi), dtype=float)


def cs_upper_bound(values: Sequence[float], alpha: float = 0.05, rho: float = 1.0) -> float:
    """One-sided confidence sequence (Howard et al. normal mixture, self-normalized) for the
    common mean of values that are conditionally symmetric about it: an upper bound valid at
    every look. ``rho`` tunes where the bound is tightest (sum of squares V near ~8·rho at
    alpha = 0.05). Returns +inf with fewer than 3 values."""
    x = np.asarray(values, dtype=float)
    if len(x) < 3:
        return math.inf
    m = float(x.mean())
    return float(cs_upper_from_stats(float(len(x)), m, float(np.sum((x - m) ** 2)), alpha, rho))


def _block_contrasts(values: Sequence[float], blocks: Sequence[str], arms: Sequence[int]) -> list[float]:
    order: list[str] = []
    seen: dict[str, tuple[list[float], list[float]]] = {}
    for v, b, a in zip(values, blocks, arms, strict=True):
        if b not in seen:
            seen[b] = ([], [])
            order.append(b)
        seen[b][0 if a else 1].append(v)
    return [float(np.mean(t) - np.mean(c)) for t, c in (seen[b] for b in order) if t and c]


def arm_harm_bound(
    treated: Sequence[float],
    control: Sequence[float],
    alpha: float = 0.05,
    *,
    treated_blocks: Sequence[str] | None = None,
    control_blocks: Sequence[str] | None = None,
    rho: float = ARM_HARM_RHO,
) -> float:
    """Upper confidence-sequence bound on the treated-minus-control mean of log(y/f), valid at
    every look (MODEL §6.4). The arm pauses when this falls below ``ARM_HARM_THRESHOLD`` (log 0.8).

    With blocks (preferred): one contrast per randomization block that has both arms,
    mean(treated) - mean(control), in block order. Under a constant effect mu these are exactly
    symmetric about mu by the design (``assign_block`` is invariant under swapping arms), whatever
    the outcome distribution, day shocks or forecast bias. Pass only blocks whose every post has
    matured, so that a block's contrast never changes after it enters the sequence.

    Without blocks: the j-th treated value is paired with the j-th control value (both in posting
    order); the differences are symmetric when the arms' noise is exchangeable.
    """
    if (treated_blocks is None) != (control_blocks is None):
        raise ValueError("give blocks for both arms or for neither")
    if treated_blocks is not None and control_blocks is not None:
        # Blocks enter in order of their first treated post (posting order), so the sequence only
        # ever grows at the end as blocks mature.
        z = _block_contrasts(
            [*treated, *control], [*treated_blocks, *control_blocks], [1] * len(treated) + [0] * len(control)
        )
    else:
        m = min(len(treated), len(control))
        z = [treated[j] - control[j] for j in range(m)]
    return cs_upper_bound(z, alpha, rho)


# ---------------------------------------------------------------- power (MODEL §6.8)


@dataclass(frozen=True)
class PowerPreview:
    n_posts: int
    power: dict[str, float]  # "1.25x", "1.5x", "2x"
    mde_ratio_80: float


def power_preview(n_posts: int, sigma: float = 1.1, r2: float = 0.3, alpha: float = 0.05) -> PowerPreview:
    """The three-number preview: power at 1.25x / 1.5x / 2x and the MDE at 80% power, from
    SE = 2·sigma·sqrt(1 - R²)/sqrt(n) (MODEL §6.8).

    Power uses that SE with a t reference (noncentral t, n - 4 degrees of freedom for the
    ANCOVA with Lin interaction, n - 2 without a covariate). The normal reference overstates
    power at small n (0.51 vs the simulated 0.46 at n = 28, R² = 0.3, 2x); the t version is within
    ±0.02 of every simulated cell of the §6.8 table. The MDE is the §6.8 normal formula, which is
    how the table's MDE column was computed."""
    se = 2 * sigma * math.sqrt(1 - r2) / math.sqrt(max(n_posts, 1))
    z = sps.norm.ppf(1 - alpha / 2)
    df = max(n_posts - (4 if r2 > 0 else 2), 1)
    t_crit = sps.t.ppf(1 - alpha / 2, df)

    def pw(lift: float) -> float:
        d = math.log(lift) / se
        return float(sps.nct.sf(t_crit, df, d) + sps.nct.cdf(-t_crit, df, d))

    mde = math.exp((z + sps.norm.ppf(0.8)) * se)
    return PowerPreview(
        n_posts,
        {"1.25x": round(pw(1.25), 2), "1.5x": round(pw(1.5), 2), "2x": round(pw(2.0), 2)},
        round(mde, 2),
    )
