"""Monte Carlo follower forecast (MODEL.md §2c "Forecast"): 4,000 paths, daily steps up to 90 days.

Each path draws the static parameters from the fit's (Laplace or prior) covariance and the
organic level jointly with them, then steps day by day:

    planned posts (schedule) → I_p from the caller's impression sampler at the path's F
    → X_d = Σ I_p / 1000 (L3), × profile clicks per 1k (L2) → λ_d = exp(η_d) + π·X_d
    → gain intensity a_d = λ_d·u_d,  loss intensity b_d = rho_d·F̃_d·v_d   (u, v unit-mean gammas)
    → F̃_{d+1} = F̃_d + a_d - b_d

η and log rho are random walks with the discount-implied daily variance C·(1/δ - 1). The gamma
shape per day is φ / D̄ (D̄ = the fit's mean interval length), so a typical interval's summed
gains have the fitted interval-level NB2 dispersion. Given the intensities, gains and losses are
Poisson, so F_T = F_0 + Poisson(Σa) - Poisson(Σb): the NB2 draws, as gamma-Poisson mixtures.

**Approximation (stated).** Losses and reach feed back through F̃, the path's intensity-implied
follower count, rather than through the realized integer count. The two differ by the Poisson
noise (about √(gains + losses)), which changes the loss mean by rho·√(...) a day: negligible.
It makes F_T | intensities exactly Skellam, which the goal probabilities use below.

**Goal probabilities with variance reduction.** The naive 4,000-path estimate of P(F_T ≥ g) has a
Monte Carlo SE of ~0.8 points at p = 0.5. Instead, on the days that need probabilities:
1. Rao-Blackwellisation: the exact conditional P(Skellam(A_T, B_T) ≥ g - F_0) per path.
2. Gauss-Hermite integration (20 nodes) over the normal behind π, the dominant and most nonlinear
   input. The Cholesky factor is ordered so that normal moves only t_π (and η_0 through its
   sensitivity), so each node reruns only the cheap intensity recursion on the same gammas and
   impressions.
3. Regression control variates with exactly known means: (a) every other random input is driven
   by independent standard normals (gammas and impressions by inverse CDF), so block scores of
   those normals are exactly N(0, 1) and independent, and probit functions of any fixed linear
   index of them have closed-form means (the index along F_T's conditional mean, and along the
   Stein direction of the conditional probabilities); (b) martingale sums such as
   Σ_d λ_d·(u_d - 1), whose increments have conditional mean zero.
   The estimate is the intercept of the regression of the integrated conditional probabilities
   on the centred controls (O(1/n) bias from the fitted coefficients).
With 4,000 paths this matches a 100,000-path reference within ±1 point (tests/test_followers.py).

Everything is seeded from ``seed`` through named streams, so a forecast is bit-for-bit
reproducible from its manifest (``forecast_from_manifest``).
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray
from scipy import optimize, special, stats

from socialmgr.modelling.followers.model import FollowerFit

FORECAST_VERSION = "followers-mc@0.1"
DEFAULT_PATHS = 4000
MAX_HORIZON_DAYS = 90
DEFAULT_CHECKPOINTS = (7, 30, 90)
QUANTILE_LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
QUANTILE_KEYS = ("q05", "q10", "q25", "q50", "q75", "q90", "q95")
GH_NODES = 20  # Gauss-Hermite nodes over π's normal

Schedule = Mapping[str, float]  # posts per day by post type


@runtime_checkable
class ImpressionSampler(Protocol):
    """Per-post impressions as a quantile function of u ∈ (0, 1) at each path's follower count."""

    @property
    def sampler_id(self) -> str: ...

    def post_types(self) -> frozenset[str]: ...

    def quantile(
        self, post_type: str, u: NDArray[np.float64], followers: NDArray[np.float64]
    ) -> NDArray[np.float64]: ...

    def to_dict(self) -> dict[str, Any] | None:
        """A JSON form for the manifest, or None when the sampler can't be serialised."""
        ...


@dataclass(frozen=True)
class ArrayImpressionSampler:
    """Arrays of predictive I48 draws by post type, made at ``followers_ref``.

    A draw at u is the empirical quantile, scaled by (F / followers_ref)^elasticity. Elasticity 0
    keeps reach at the current F; pass e.g. β × the in-network share from 2a to let reach grow with F.
    """

    draws: Mapping[str, tuple[float, ...]]
    followers_ref: int
    elasticity: float = 0.0
    _sorted: dict[str, NDArray[np.float64]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        for k, v in self.draws.items():
            arr = np.sort(np.asarray(v, dtype=np.float64))
            if arr.size == 0:
                raise ValueError(f"no draws for post type {k}")
            self._sorted[k] = arr

    @property
    def sampler_id(self) -> str:
        return "arrays"

    @property
    def uses_followers(self) -> bool:
        return self.elasticity != 0.0

    def post_types(self) -> frozenset[str]:
        return frozenset(self.draws)

    def quantile(
        self, post_type: str, u: NDArray[np.float64], followers: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        arr = self._sorted[post_type]
        idx = np.minimum((u * arr.size).astype(np.int64), arr.size - 1)
        out = arr[idx]
        if self.elasticity:
            ratio = np.maximum(followers, 1.0) / max(self.followers_ref, 1)
            out = out * np.power(ratio, self.elasticity)
        return np.asarray(out, dtype=np.float64)

    def mean(self, post_type: str, followers: NDArray[np.float64]) -> NDArray[np.float64]:
        """E[draw | F]: lets the forecast use the impression noise as a zero-mean control."""
        base = float(self._sorted[post_type].mean())
        if not self.elasticity:
            return np.full(followers.shape, base)
        ratio = np.maximum(followers, 1.0) / max(self.followers_ref, 1)
        return np.asarray(base * np.power(ratio, self.elasticity), dtype=np.float64)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "arrays",
            "draws": {k: [float(x) for x in v] for k, v in sorted(self.draws.items())},
            "followers_ref": self.followers_ref,
            "elasticity": self.elasticity,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ArrayImpressionSampler":
        return cls(
            draws={k: tuple(float(x) for x in v) for k, v in d["draws"].items()},
            followers_ref=int(d["followers_ref"]),
            elasticity=float(d.get("elasticity", 0.0)),
        )


@dataclass(frozen=True)
class FollowerGoal:
    target: int
    horizon_days: int

    def __post_init__(self) -> None:
        if not 1 <= self.horizon_days <= MAX_HORIZON_DAYS:
            raise ValueError(f"goal horizon must be 1..{MAX_HORIZON_DAYS} days")
        if self.target < 1:
            raise ValueError("goal target must be at least 1 follower")

    @classmethod
    def from_date(cls, target: int, target_date: datetime, as_of: datetime) -> "FollowerGoal":
        days = math.ceil((target_date - as_of).total_seconds() / 86_400.0)
        return cls(target, max(1, days))

    def to_dict(self) -> dict[str, int]:
        return {"target": self.target, "horizon_days": self.horizon_days}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FollowerGoal":
        return cls(int(d["target"]), int(d["horizon_days"]))


@dataclass(frozen=True)
class Scenario:
    name: str
    schedule: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "schedule": dict(sorted(self.schedule.items()))}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Scenario":
        return cls(str(d["name"]), {k: float(v) for k, v in d["schedule"].items()})


DEFAULT_SCENARIOS: tuple[Scenario, ...] = (
    Scenario("2 originals a day", {"original": 2.0}),
    Scenario("3 originals a day", {"original": 3.0}),
    Scenario("4 originals a day", {"original": 4.0}),
    Scenario("2 originals and 3 replies a day", {"original": 2.0, "reply": 3.0}),
)


# --- random streams -------------------------------------------------------------------------------


class _Streams:
    def __init__(self, seed: int) -> None:
        self.seed = int(seed)

    def gen(self, label: str) -> np.random.Generator:
        key = int.from_bytes(hashlib.sha256(label.encode()).digest()[:4], "big")
        return np.random.default_rng(np.random.SeedSequence([self.seed, key]))

    def normal(self, label: str, shape: int | tuple[int, ...]) -> NDArray[np.float64]:
        return self.gen(label).standard_normal(shape)

    def uniform(self, label: str, shape: int | tuple[int, ...]) -> NDArray[np.float64]:
        return self.gen(label).uniform(size=shape)


def _cholesky(cov: NDArray[np.float64]) -> NDArray[np.float64]:
    """Lower-triangular factor (with a small jitter if needed), so the last normal moves only the
    last coordinate."""
    jitter = 0.0
    scale = float(np.mean(np.diag(cov))) or 1.0
    for _ in range(8):
        try:
            return np.linalg.cholesky(cov + jitter * np.eye(cov.shape[0]))
        except np.linalg.LinAlgError:
            jitter = max(jitter * 10.0, 1e-10 * scale)
    raise ValueError("parameter covariance is not positive definite")


def _unit_gamma(shape: NDArray[np.float64], z: NDArray[np.float64]) -> NDArray[np.float64]:
    u = np.clip(special.ndtr(z), 1e-15, 1.0 - 1e-15)
    return np.asarray(special.gammaincinv(shape, u) / shape)


def _pi_of(t: NDArray[np.float64], level: str) -> NDArray[np.float64]:
    if level == "L2":
        return np.asarray(special.expit(t))
    if level == "L3":
        return np.exp(t)
    return np.zeros_like(t)


@dataclass
class _Paths:
    f0: int
    a: NDArray[np.float64]  # (T, n) cumulative gain intensity through day t, sampled π
    b: NDArray[np.float64]  # (T, n) cumulative loss intensity, sampled π
    node_weights: NDArray[np.float64]  # (K,) Gauss-Hermite weights over π's normal, summing to 1
    node_a: dict[int, NDArray[np.float64]]  # day -> (K, n) cumulative gain intensity at each node
    node_b: dict[int, NDArray[np.float64]]
    scores: dict[int, NDArray[np.float64]]  # day -> (n, J) independent N(0, 1) block scores
    martingales: dict[int, NDArray[np.float64]]  # day -> (n, 4) controls with exactly zero mean
    seed: int

    def draws(self, day: int) -> NDArray[np.int64]:
        rng = _Streams(self.seed).gen(f"draws:{day}")
        a, b = self.a[day - 1], self.b[day - 1]
        f = self.f0 + rng.poisson(a) - rng.poisson(b)
        return np.asarray(np.maximum(f, 0), dtype=np.int64)

    def _skellam_sf(
        self, threshold: int, a: NDArray[np.float64], b: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        h = stats.skellam.sf(threshold - self.f0 - 1, a, np.maximum(b, 1e-300))
        return np.asarray(np.nan_to_num(h, nan=0.0), dtype=np.float64)

    def prob_ge(self, day: int, threshold: int) -> float:
        if day not in self.node_a:
            return float(
                np.clip(self._skellam_sf(threshold, self.a[day - 1], self.b[day - 1]).mean(), 0.0, 1.0)
            )
        na, nb, w = self.node_a[day], self.node_b[day], self.node_weights
        h = w @ self._skellam_sf(threshold, na, nb)
        mu = self.f0 + w @ (na - nb)
        return _control_variate_estimate(
            h, mu, w @ (na + nb), self.scores[day], self.martingales[day], threshold
        )


def _control_variate_estimate(
    h: NDArray[np.float64],
    mu: NDArray[np.float64],
    poisson_var: NDArray[np.float64],
    scores: NDArray[np.float64],
    martingales: NDArray[np.float64],
    threshold: int,
) -> float:
    """Regression estimate of E[h] with controls of exactly known mean (module docstring)."""
    if float(h.max() - h.min()) < 1e-12:
        return float(np.clip(h.mean(), 0.0, 1.0))
    n = h.size
    design = np.column_stack([np.ones(n), scores])
    coef, *_ = np.linalg.lstsq(design, mu, rcond=None)
    b = coef[1:]
    s = scores @ b
    v = float(b @ b)
    resid = mu - design @ coef
    tau = math.sqrt(float(resid.var()) + float(poisson_var.mean()) + 1e-9)
    c0 = float(coef[0]) - threshold + 0.5
    cols = []
    for scale in (0.6, 1.0, 1.6):
        t = scale * tau
        cols.append(special.ndtr((c0 + s) / t) - special.ndtr(c0 / math.sqrt(t * t + v)))
    cols.extend(_stein_controls(h, scores))
    controls = np.column_stack([np.ones(n), *cols, scores, martingales])
    beta, *_ = np.linalg.lstsq(controls, h, rcond=None)
    return float(np.clip(beta[0], 0.0, 1.0))


def _stein_controls(h: NDArray[np.float64], scores: NDArray[np.float64]) -> list[NDArray[np.float64]]:
    """Probit controls along the average-gradient direction of h.

    For independent N(0, 1) scores, the OLS slope of h on the scores is E[∇h] (Stein's lemma),
    the best single index for h. Along the unit direction u ~ N(0, 1), a probit link Φ(a + b·u)
    is fitted and a few variants around it are used as controls: E[Φ(a + b·u)] = Φ(a / √(1 + b²)).
    """
    n = h.size
    slope, *_ = np.linalg.lstsq(np.column_stack([np.ones(n), scores]), h, rcond=None)
    d = slope[1:]
    norm = float(np.linalg.norm(d))
    if norm < 1e-12:
        return []
    u = scores @ (d / norm)
    p = min(max(float(h.mean()), 1e-4), 1.0 - 1e-4)

    def resid(x: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray(special.ndtr(x[0] + x[1] * u) - h)

    x0 = np.array([float(special.ndtri(p)) * math.sqrt(2.0), 1.0])
    fit = optimize.least_squares(resid, x0, bounds=([-40.0, 1e-3], [40.0, 200.0]), max_nfev=200)
    alpha, beta = float(fit.x[0]), float(fit.x[1])
    centre = -alpha / beta
    cols = []
    for scale in (0.5, 1.0, 2.0):
        b = beta * scale
        for shift in (-0.25, 0.0, 0.25):
            a = -(centre + shift) * b
            cols.append(special.ndtr(a + b * u) - special.ndtr(a / math.sqrt(1.0 + b * b)))
    return cols


@dataclass
class _Run:
    a: NDArray[np.float64] | None
    b: NDArray[np.float64] | None
    a_at: dict[int, NDArray[np.float64]]
    b_at: dict[int, NDArray[np.float64]]
    martingales: dict[int, NDArray[np.float64]]


@dataclass
class _Common:
    """Everything a path needs that doesn't depend on the schedule: drawn once per forecast and
    shared by the main schedule and the what-if scenarios (common random numbers)."""

    n: int
    horizon: int
    seed: int
    level: str
    pc_per_1k: float
    z_static: NDArray[np.float64]  # (n, p) in the factor's order, π's normal last
    t_pi: NDArray[np.float64]
    t_pi_base: NDArray[np.float64]  # t_π with π's normal set to 0
    pi_sd: float
    eta_pi_slope: float  # dη_0 / d(π's normal)
    eta0: NDArray[np.float64]
    eta0_base: NDArray[np.float64]
    z_eta0: NDArray[np.float64]
    z_rho0: NDArray[np.float64]
    walk: NDArray[np.float64]  # (T, n) η_d - η_0
    walk_step: NDArray[np.float64]  # (T, n) martingale increments of exp(walk)
    rho: NDArray[np.float64]  # (T, n) daily loss rate
    unit_g: NDArray[np.float64]  # (T, n) unit-mean gamma multipliers for gains
    unit_l: NDArray[np.float64]
    open_day: NDArray[np.bool_] | None  # (T, n) not a structural-zero day
    int_eta: NDArray[np.float64]  # (T, n) Σ_t Σ_{j≤t} ε_j for the score
    int_rho: NDArray[np.float64]
    sum_zg: NDArray[np.float64]
    sum_zl: NDArray[np.float64]


def _draw_common(fit: FollowerFit, horizon: int, n: int, seed: int) -> _Common:
    rs = _Streams(seed)
    names = list(fit.theta_names)
    p = len(names)
    i_pi = names.index("t_pi")
    order = [j for j in range(p) if j != i_pi] + [i_pi]  # π's normal last: it moves only t_π
    theta = np.asarray(fit.theta, dtype=np.float64)
    cov = np.asarray(fit.theta_cov, dtype=np.float64)
    lower = _cholesky(cov[np.ix_(order, order)])
    z_static = rs.normal("static", (n, p))
    dev = np.empty((n, p))
    dev[:, order] = z_static @ lower.T
    th = theta + dev
    z_pi = z_static[:, -1]
    pi_sd = float(lower[-1, -1])
    phi_g = np.exp(th[:, names.index("log_phi_gain")])
    phi_l = np.exp(th[:, names.index("log_phi_loss")])
    omega = special.expit(th[:, names.index("logit_omega")]) if fit.zero_inflated else None

    sens = np.asarray(fit.eta_sens, dtype=np.float64)
    z_eta0 = rs.normal("eta0", n)
    z_rho0 = rs.normal("rho0", n)
    eta0 = fit.eta_mean + dev @ sens + math.sqrt(fit.eta_var) * z_eta0
    slope = float(sens[i_pi]) * pi_sd
    log_rho0 = fit.log_rho_mean + math.sqrt(fit.log_rho_var) * z_rho0
    w_eta = math.sqrt(fit.eta_var * (1.0 / fit.config.gain_discount - 1.0))
    w_rho = math.sqrt(fit.log_rho_var * (1.0 / fit.config.loss_discount - 1.0))
    eps_eta = rs.normal("walk_eta", (horizon, n))
    eps_rho = rs.normal("walk_rho", (horizon, n))
    z_gain = rs.normal("gamma_gain", (horizon, n))
    z_loss = rs.normal("gamma_loss", (horizon, n))
    dbar = max(fit.mean_interval_days, 1e-3)
    walk = w_eta * np.cumsum(eps_eta, axis=0)
    prev_walk = np.vstack([np.zeros((1, n)), walk[:-1]])
    cum_eta = np.cumsum(eps_eta, axis=0)
    cum_rho = np.cumsum(eps_rho, axis=0)
    return _Common(
        n=n,
        horizon=horizon,
        seed=seed,
        level=fit.level,
        pc_per_1k=fit.pc_per_1k,
        z_static=z_static,
        t_pi=th[:, i_pi],
        t_pi_base=th[:, i_pi] - pi_sd * z_pi,
        pi_sd=pi_sd,
        eta_pi_slope=slope,
        eta0=eta0,
        eta0_base=eta0 - slope * z_pi,
        z_eta0=z_eta0,
        z_rho0=z_rho0,
        walk=walk,
        walk_step=np.exp(prev_walk) * (np.exp(w_eta * eps_eta) - math.exp(0.5 * w_eta * w_eta)),
        rho=np.exp(log_rho0 + w_rho * cum_rho),
        unit_g=_unit_gamma(np.broadcast_to(phi_g / dbar, (horizon, n)), z_gain),
        unit_l=_unit_gamma(np.broadcast_to(phi_l / dbar, (horizon, n)), z_loss),
        open_day=(rs.uniform("structural_zero", (horizon, n)) >= omega) if omega is not None else None,
        int_eta=np.cumsum(cum_eta, axis=0),
        int_rho=np.cumsum(cum_rho, axis=0),
        sum_zg=np.cumsum(z_gain, axis=0),
        sum_zl=np.cumsum(z_loss, axis=0),
    )


def _simulate(
    c: _Common,
    f0: int,
    schedule: Schedule,
    sampler: ImpressionSampler,
    score_days: Sequence[int],
) -> _Paths:
    rs = _Streams(c.seed)
    n, horizon = c.n, c.horizon
    slots: list[tuple[str, NDArray[np.float64], float, NDArray[np.bool_] | None, NDArray[np.float64]]] = []
    for post_type, rate in sorted(schedule.items()):
        if rate <= 0:
            continue
        whole = math.floor(rate)
        frac = rate - whole
        for j in range(math.ceil(rate)):
            z = rs.normal(f"impressions:{post_type}:{j}", (horizon, n))
            present = rs.uniform(f"count:{post_type}:{j}", (horizon, n)) < frac if j >= whole else None
            slots.append((post_type, special.ndtr(z), frac if j >= whole else 1.0, present, z))
    x_scale = c.pc_per_1k / 1000.0 if c.level == "L2" else (1.0 / 1000.0 if c.level == "L3" else 0.0)
    mean_fn = getattr(sampler, "mean", None)
    uses_followers = bool(getattr(sampler, "uses_followers", True))
    fixed_imps: tuple[NDArray[np.float64], NDArray[np.float64]] | None = None
    if not slots:
        fixed_imps = (np.zeros((horizon, n)), np.zeros((horizon, n)))
    elif not uses_followers:
        fixed_imps = _impressions(slots, sampler, mean_fn, np.full(n, float(f0)), None)
    wanted = sorted(set(score_days))

    def run(eta_start: NDArray[np.float64], pi: NDArray[np.float64], keep_all: bool) -> _Run:
        f_tilde = np.full(n, float(f0))
        cum_a = np.zeros(n)
        cum_b = np.zeros(n)
        a_all = np.empty((horizon, n)) if keep_all else None
        b_all = np.empty((horizon, n)) if keep_all else None
        mg_gain = np.zeros(n)
        mg_loss = np.zeros(n)
        mg_imp = np.zeros(n)
        mg_walk = np.zeros(n)
        int_mg_walk = np.zeros(n)
        organic0 = np.exp(eta_start)
        a_at: dict[int, NDArray[np.float64]] = {}
        b_at: dict[int, NDArray[np.float64]] = {}
        mart: dict[int, NDArray[np.float64]] = {}
        for d in range(horizon):
            if fixed_imps is not None:
                imps, exp_imps = fixed_imps[0][d], fixed_imps[1][d]
            else:
                imps, exp_imps = _impressions(slots, sampler, mean_fn, f_tilde, d)
            lam = organic0 * np.exp(c.walk[d]) + pi * imps * x_scale
            if c.open_day is not None:
                lam = lam * c.open_day[d]
            loss_mean = c.rho[d] * np.maximum(f_tilde, 0.0)
            gain = lam * c.unit_g[d]
            loss = loss_mean * c.unit_l[d]
            cum_a += gain
            cum_b += loss
            f_tilde = f_tilde + gain - loss
            mg_gain += lam * (c.unit_g[d] - 1.0)
            mg_loss += loss_mean * (c.unit_l[d] - 1.0)
            if mean_fn is not None:
                mg_imp += pi * x_scale * (imps - exp_imps)
            mg_walk += organic0 * c.walk_step[d]
            int_mg_walk += mg_walk
            if a_all is not None and b_all is not None:
                a_all[d] = cum_a
                b_all[d] = cum_b
            if d + 1 in wanted:
                a_at[d + 1] = cum_a.copy()
                b_at[d + 1] = cum_b.copy()
                mart[d + 1] = np.column_stack([mg_gain, mg_loss, mg_imp, int_mg_walk])
        return _Run(a_all, b_all, a_at, b_at, mart)

    main = run(c.eta0, _pi_of(c.t_pi, c.level), keep_all=True)
    assert main.a is not None and main.b is not None
    # Gauss-Hermite over π's normal: exact integration of the dominant nonlinear input
    if c.level == "L0" or c.pi_sd <= 0.0 or not wanted:
        node_x, node_w = np.zeros(1), np.ones(1)
    else:
        node_x, node_w = np.polynomial.hermite_e.hermegauss(GH_NODES)
        node_w = node_w / node_w.sum()
    runs = [
        run(c.eta0_base + c.eta_pi_slope * x, _pi_of(c.t_pi_base + c.pi_sd * x, c.level), keep_all=False)
        for x in (node_x if wanted else [])
    ]
    node_a = {d: np.vstack([r.a_at[d] for r in runs]) for d in wanted}
    node_b = {d: np.vstack([r.b_at[d] for r in runs]) for d in wanted}
    martingales = {d: sum(w * r.martingales[d] for w, r in zip(node_w, runs, strict=True)) for d in wanted}

    # block scores, all exactly N(0, 1) and mutually independent (π's normal is integrated out)
    sum_imp = np.cumsum(sum(s[4] for s in slots), axis=0) if slots else None
    scores: dict[int, NDArray[np.float64]] = {}
    for day in wanted:
        norm_walk = math.sqrt(sum(k * k for k in range(1, day + 1)))
        cols = [
            *c.z_static[:, :-1].T,
            c.z_eta0,
            c.z_rho0,
            c.int_eta[day - 1] / norm_walk,
            c.int_rho[day - 1] / norm_walk,
            c.sum_zg[day - 1] / math.sqrt(day),
            c.sum_zl[day - 1] / math.sqrt(day),
        ]
        if sum_imp is not None:
            cols.append(sum_imp[day - 1] / math.sqrt(day * len(slots)))
        scores[day] = np.column_stack(cols)
    return _Paths(
        f0=f0,
        a=main.a,
        b=main.b,
        node_weights=np.asarray(node_w, dtype=np.float64),
        node_a=node_a,
        node_b=node_b,
        scores=scores,
        martingales={d: np.asarray(m, dtype=np.float64) for d, m in martingales.items()},
        seed=c.seed,
    )


def _impressions(
    slots: Sequence[tuple[str, NDArray[np.float64], float, NDArray[np.bool_] | None, NDArray[np.float64]]],
    sampler: ImpressionSampler,
    mean_fn: Any,
    followers: NDArray[np.float64],
    day: int | None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Summed impressions and their conditional mean, for one day or (day None) every day."""
    shape = followers.shape if day is not None else slots[0][1].shape
    imps = np.zeros(shape)
    expected = np.zeros(shape)
    for post_type, u, share, present, _ in slots:
        uu = u[day] if day is not None else u
        draw = sampler.quantile(post_type, uu, followers)
        if present is not None:
            draw = draw * (present[day] if day is not None else present)
        imps = imps + draw
        if mean_fn is not None:
            expected = expected + share * mean_fn(post_type, followers)
    return imps, expected


# --- outputs ------------------------------------------------------------------------------------------


def _quantiles(draws: NDArray[np.int64]) -> dict[str, float]:
    qs = np.quantile(draws, QUANTILE_LEVELS)
    return {k: float(v) for k, v in zip(QUANTILE_KEYS, qs, strict=True)}


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    schedule: Mapping[str, float]
    quantiles: Mapping[int, Mapping[str, float]]
    p_goal: float | None
    skipped: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "schedule": dict(self.schedule),
            "quantiles": {str(k): dict(v) for k, v in self.quantiles.items()},
            "p_goal": self.p_goal,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ScenarioResult":
        return cls(
            name=d["name"],
            schedule={k: float(v) for k, v in d["schedule"].items()},
            quantiles={int(k): {q: float(x) for q, x in v.items()} for k, v in d["quantiles"].items()},
            p_goal=None if d.get("p_goal") is None else float(d["p_goal"]),
            skipped=d.get("skipped"),
        )


@dataclass(frozen=True, eq=False)
class FollowerForecast:
    """A follower forecast. ``draws`` holds the integer F draws at each checkpoint (for freezing
    and later scoring); goal probabilities use the variance-reduced estimator while the paths are
    in memory, and the draws otherwise (after ``from_dict``)."""

    version: str
    model_version: str
    fit_label: str
    level: str
    as_of: datetime
    followers_now: int
    seed: int
    n_paths: int
    horizon_days: int
    checkpoints: tuple[int, ...]
    quantiles: Mapping[int, Mapping[str, float]]
    goal: FollowerGoal | None
    p_goal: float | None
    scenarios: tuple[ScenarioResult, ...]
    draws: Mapping[int, NDArray[np.int64]]
    manifest: Mapping[str, Any]
    manifest_hash: str
    _paths: _Paths | None = field(default=None, repr=False)

    def prob_ge(self, day: int, threshold: int) -> float:
        """P(F_day ≥ threshold)."""
        if self._paths is not None:
            return self._paths.prob_ge(day, int(threshold))
        if day in self.draws:
            return float(np.mean(self.draws[day] >= threshold))
        raise KeyError(f"no paths or draws for day {day}")

    def prob_gt(self, day: int, value: float) -> float:
        """P(F_day > value)."""
        return self.prob_ge(day, math.floor(value) + 1)

    def prob_lt(self, day: int, value: float) -> float:
        """P(F_day < value)."""
        return 1.0 - self.prob_ge(day, math.ceil(value))

    def draws_at(self, day: int) -> NDArray[np.int64]:
        if day in self.draws:
            return self.draws[day]
        if self._paths is not None:
            return self._paths.draws(day)
        raise KeyError(f"no draws for day {day}")

    def expected_growth(self, day: int) -> float:
        return float(np.median(self.draws_at(day))) - self.followers_now

    def to_dict(self, include_draws: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "model_version": self.model_version,
            "fit_label": self.fit_label,
            "level": self.level,
            "as_of": self.as_of.isoformat(),
            "followers_now": self.followers_now,
            "seed": self.seed,
            "n_paths": self.n_paths,
            "horizon_days": self.horizon_days,
            "checkpoints": list(self.checkpoints),
            "quantiles": {str(k): dict(v) for k, v in self.quantiles.items()},
            "goal": None if self.goal is None else self.goal.to_dict(),
            "p_goal": self.p_goal,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "manifest_hash": self.manifest_hash,
            "manifest": dict(self.manifest),
        }
        if include_draws:
            out["draws"] = {str(k): [int(x) for x in v] for k, v in self.draws.items()}
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FollowerForecast":
        return cls(
            version=d["version"],
            model_version=d["model_version"],
            fit_label=d["fit_label"],
            level=d["level"],
            as_of=datetime.fromisoformat(d["as_of"]),
            followers_now=int(d["followers_now"]),
            seed=int(d["seed"]),
            n_paths=int(d["n_paths"]),
            horizon_days=int(d["horizon_days"]),
            checkpoints=tuple(int(x) for x in d["checkpoints"]),
            quantiles={int(k): {q: float(x) for q, x in v.items()} for k, v in d["quantiles"].items()},
            goal=None if d.get("goal") is None else FollowerGoal.from_dict(d["goal"]),
            p_goal=None if d.get("p_goal") is None else float(d["p_goal"]),
            scenarios=tuple(ScenarioResult.from_dict(s) for s in d.get("scenarios", [])),
            draws={int(k): np.asarray(v, dtype=np.int64) for k, v in d.get("draws", {}).items()},
            manifest=d["manifest"],
            manifest_hash=d["manifest_hash"],
        )


def manifest_hash(manifest: Mapping[str, Any]) -> str:
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def forecast_followers(
    fit: FollowerFit,
    schedule: Schedule,
    sampler: ImpressionSampler,
    *,
    followers_now: int | None = None,
    as_of: datetime | None = None,
    goal: FollowerGoal | None = None,
    horizon_days: int = MAX_HORIZON_DAYS,
    checkpoints: Sequence[int] = DEFAULT_CHECKPOINTS,
    n_paths: int = DEFAULT_PATHS,
    seed: int = 0,
    scenarios: Sequence[Scenario] | None = DEFAULT_SCENARIOS,
    probability_days: Sequence[int] = (),
) -> FollowerForecast:
    """Forecast F over the next ``horizon_days`` days under ``schedule`` (posts per day by type).

    ``prob_ge`` uses the variance-reduced estimator on the goal's day and on ``probability_days``;
    other days fall back to the Rao-Blackwellised mean, which is unbiased but noisier.
    """
    f0 = followers_now if followers_now is not None else fit.followers_last
    when = as_of if as_of is not None else fit.last_end
    if f0 is None or when is None:
        raise ValueError("followers_now and as_of are required when the fit has no intervals")
    if when.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    if not 1 <= horizon_days <= MAX_HORIZON_DAYS:
        raise ValueError(f"horizon must be 1..{MAX_HORIZON_DAYS} days")
    missing = set(schedule) - sampler.post_types()
    if missing:
        raise ValueError(f"sampler has no draws for post types {sorted(missing)}")
    goal_days = {goal.horizon_days} if goal else set()
    days = sorted({d for d in checkpoints if d <= horizon_days} | goal_days)
    prob_days = sorted({int(d) for d in probability_days if 1 <= d <= horizon_days} | goal_days)
    if goal is not None and goal.horizon_days > horizon_days:
        raise ValueError("goal horizon is beyond the forecast horizon")
    sampler_dict = sampler.to_dict()
    manifest: dict[str, Any] = {
        "version": FORECAST_VERSION,
        "seed": int(seed),
        "n_paths": int(n_paths),
        "horizon_days": int(horizon_days),
        "checkpoints": [int(d) for d in checkpoints],
        "as_of": when.isoformat(),
        "followers_now": int(f0),
        "schedule": {k: float(v) for k, v in sorted(schedule.items())},
        "goal": None if goal is None else goal.to_dict(),
        "scenarios": None if scenarios is None else [s.to_dict() for s in scenarios],
        "probability_days": prob_days,
        "fit": fit.to_dict(),
        "sampler": sampler_dict
        if sampler_dict is not None
        else {"kind": "external", "id": sampler.sampler_id},
    }
    common = _draw_common(fit, horizon_days, n_paths, seed)
    paths = _simulate(common, int(f0), schedule, sampler, prob_days)
    draws = {d: paths.draws(d) for d in days}
    p_goal = paths.prob_ge(goal.horizon_days, goal.target) if goal else None

    results: list[ScenarioResult] = []
    for sc in scenarios or ():
        lacking = set(sc.schedule) - sampler.post_types()
        if lacking:
            results.append(ScenarioResult(sc.name, sc.schedule, {}, None, f"no draws for {sorted(lacking)}"))
            continue
        sp = _simulate(common, int(f0), sc.schedule, sampler, sorted(goal_days))
        results.append(
            ScenarioResult(
                sc.name,
                dict(sc.schedule),
                {d: _quantiles(sp.draws(d)) for d in days},
                sp.prob_ge(goal.horizon_days, goal.target) if goal else None,
            )
        )
    return FollowerForecast(
        version=FORECAST_VERSION,
        model_version=fit.version,
        fit_label=fit.label,
        level=fit.level,
        as_of=when,
        followers_now=int(f0),
        seed=int(seed),
        n_paths=int(n_paths),
        horizon_days=int(horizon_days),
        checkpoints=tuple(days),
        quantiles={d: _quantiles(v) for d, v in draws.items()},
        goal=goal,
        p_goal=p_goal,
        scenarios=tuple(results),
        draws=draws,
        manifest=manifest,
        manifest_hash=manifest_hash(manifest),
        _paths=paths,
    )


def forecast_from_manifest(
    manifest: Mapping[str, Any], sampler: ImpressionSampler | None = None
) -> FollowerForecast:
    """Recompute a forecast bit-for-bit from its manifest. An external sampler must be passed again."""
    if manifest["version"] != FORECAST_VERSION:
        raise ValueError(f"manifest is for {manifest['version']}, this engine is {FORECAST_VERSION}")
    if sampler is None:
        if manifest["sampler"].get("kind") != "arrays":
            raise ValueError("this manifest used an external sampler; pass it again")
        sampler = ArrayImpressionSampler.from_dict(manifest["sampler"])
    scenarios = manifest.get("scenarios")
    return forecast_followers(
        FollowerFit.from_dict(manifest["fit"]),
        manifest["schedule"],
        sampler,
        followers_now=int(manifest["followers_now"]),
        as_of=datetime.fromisoformat(manifest["as_of"]),
        goal=None if manifest.get("goal") is None else FollowerGoal.from_dict(manifest["goal"]),
        horizon_days=int(manifest["horizon_days"]),
        checkpoints=tuple(int(d) for d in manifest["checkpoints"]),
        n_paths=int(manifest["n_paths"]),
        seed=int(manifest["seed"]),
        scenarios=None if scenarios is None else [Scenario.from_dict(s) for s in scenarios],
        probability_days=tuple(int(d) for d in manifest.get("probability_days", ())),
    )
