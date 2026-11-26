"""Follower DGLM over run intervals with a latent daily process (MODEL.md §2c, batch mode).

Gains over interval k of D_k days, with X_k the interval's profile clicks (level L2) or
impressions / 1000 (level L3):

    G_k ~ NB2(D_k·exp(η_k) + π·X_k, φ_G)        η: local level, discount 0.97 per day
    L_k ~ NB2(rho_k·F_{k-1}·D_k, φ_L)              log rho: local level, discount 0.99 per day
    L_k = G_k - ΔF_k                              observed only when G_k is complete

**Filter.** Each level is a Gaussian state on the log scale. Between runs its variance is
discounted, R = C / δ^{D_k} (West-Harrison discounting over D_k daily steps). At a run the state
is updated by a Laplace step: the posterior mode of N(η; m, R) × NB2(y; μ(η), φ) by Fisher
scoring, with variance 1 / (1/R + I(η̂)). This is a DGLM in the sense of West-Harrison-Migon
with a Laplace instead of a conjugate-gamma update: the conjugate update needs a log-linear mean,
and the funnel's mean exp(η) + π·X is additive. The latent daily level is summed over the interval
as D_k·exp(η_k), the per-interval form of MODEL's Σ_d λ_d.

Intervals with missing or incomplete gains skip the gain likelihood; their net change ΔF still
updates η through a Gaussian (extended Kalman) step on ΔF + E[L] using the loss filter's
predictive, and enters the likelihood as N(ΔF; E[G] - E[L], Var G + Var L).

**Static parameters.** (π, φ_G) and φ_L are MAP estimates under the priors below, from the
prediction-error decomposition of the filters' one-step predictive densities (moment-matched NB2),
with a Laplace (inverse-Hessian) covariance. Everything is deterministic: no random numbers are
drawn in fitting. The filtered level's dependence on (π, φ_G) is kept as a linear sensitivity so
forecasts draw η jointly with the static parameters. Fewer than 28 days between the first and last
interval leave the static parameters at their priors, labelled ``prior_only``.

**Zero-heavy series.** The NB2 likelihood already places mass at zero for small means. When more
than half the gain intervals are zero, the fit also tries a structural-zero probability ω (a
zero-inflated NB2 at the interval level, the static analogue of Berry-West's DCMM binary
component) and keeps it when its Laplace evidence beats plain NB2 by a Bayes factor over 10. A
plain NB2 can mimic excess zeros with a tiny φ, so the comparison is by evidence, not by a zero
count against the NB2 predictive.

**Priors** (MODEL has no explicit φ_G or π prior for the follower model; v1 uses its A7 placeholders):
- L2: logit π_pc ~ N(logit 0.1, 1²) (follows per profile click, MODEL §2a A7).
- L3: log π_I ~ N(log 0.5, 1.4²) (5 profile clicks per 1k impressions × 0.1 follows per click;
  sd √(1² + 1²)).
- log φ_G, log φ_L ~ N(log 4, 1²); η_0 ~ N(log 1, 1.5²); log rho_0 ~ N(log 0.002, 1.5²);
  logit ω ~ N(-2, 1.5²).
"""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import numpy as np
from scipy import optimize, special, stats

from socialmgr.modelling.followers.data import RunInterval, finite

MODEL_VERSION = "followers-dglm@0.1"
Level = Literal["L2", "L3", "L0"]
FitLabel = Literal["prior_only", "fitted"]

DEFAULT_PC_PER_1K = 5.0  # MODEL §2a A7: profile clicks per 1k impressions, prior log-mean log 5


@dataclass(frozen=True)
class NormalPrior:
    mean: float
    sd: float

    def to_dict(self) -> dict[str, float]:
        return {"mean": self.mean, "sd": self.sd}


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


@dataclass(frozen=True)
class FollowerPriors:
    logit_pi_pc: NormalPrior = NormalPrior(_logit(0.1), 1.0)
    log_pi_imp: NormalPrior = NormalPrior(math.log(0.5), 1.4)
    log_phi_gain: NormalPrior = NormalPrior(math.log(4.0), 1.0)
    log_phi_loss: NormalPrior = NormalPrior(math.log(4.0), 1.0)
    eta0: NormalPrior = NormalPrior(0.0, 1.5)
    log_rho0: NormalPrior = NormalPrior(math.log(0.002), 1.5)
    logit_omega: NormalPrior = NormalPrior(-2.0, 1.5)

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {k: getattr(self, k).to_dict() for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: Mapping[str, Mapping[str, float]]) -> "FollowerPriors":
        return cls(**{k: NormalPrior(float(v["mean"]), float(v["sd"])) for k, v in d.items()})


@dataclass(frozen=True)
class FollowerConfig:
    gain_discount: float = 0.97  # MODEL §2c
    loss_discount: float = 0.99
    prior_only_days: float = 28.0  # plan §6: prior-only for the first 4 weeks of counts
    zero_share: float = 0.5
    zero_log_bayes_factor: float = math.log(10.0)

    def to_dict(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: Mapping[str, float]) -> "FollowerConfig":
        return cls(**{k: float(v) for k, v in d.items()})


# --- NB2 helpers ----------------------------------------------------------------------------------


def nb_logpmf(y: float, mu: float, phi: float) -> float:
    mu = max(mu, 1e-12)
    return (
        math.lgamma(y + phi)
        - math.lgamma(phi)
        - math.lgamma(y + 1.0)
        + phi * math.log(phi / (phi + mu))
        + y * math.log(mu / (phi + mu))
    )


def _zinb_logpmf(y: float, mu: float, phi: float, omega: float) -> float:
    if omega <= 0.0:
        return nb_logpmf(y, mu, phi)
    if y == 0:
        return math.log(omega + (1.0 - omega) * math.exp(nb_logpmf(0.0, mu, phi)))
    return math.log1p(-omega) + nb_logpmf(y, mu, phi)


def _lognormal_moments(m: float, r: float, scale: float) -> tuple[float, float]:
    """Mean and variance of scale·exp(η), η ~ N(m, r)."""
    mean = scale * math.exp(m + 0.5 * r)
    var = mean * mean * math.expm1(min(r, 50.0))
    return mean, var


def _moment_nb(mean_mu: float, var_mu: float, phi: float) -> tuple[float, float, float]:
    """NB2 moment-matched to the mixture NB2(μ, φ), μ random: (mean, φ_eff, variance)."""
    var = mean_mu + (var_mu + mean_mu * mean_mu) / phi + var_mu
    excess = max(var - mean_mu, 1e-12)
    return mean_mu, max(mean_mu * mean_mu / excess, 1e-6), var


def _laplace_update(
    m: float, r: float, y: float, scale: float, c: float, phi: float, omega: float
) -> tuple[float, float]:
    """Posterior mode and variance of η for N(η; m, r) × (ZI)NB2(y; scale·e^η + c, φ)."""
    eta = m
    zero_mix = omega > 0.0 and y == 0
    info = 0.0
    for _ in range(60):
        a = scale * math.exp(eta)
        mu = a + c
        if zero_mix:
            p0 = math.exp(phi * math.log(phi / (phi + mu)))
            w = (1.0 - omega) * p0 / (omega + (1.0 - omega) * p0)
            score = w * (-phi * a / (phi + mu))
            info = w * a * a * phi / (mu * (mu + phi))
        else:
            score = a * (y / mu - (y + phi) / (mu + phi))
            info = a * a * phi / (mu * (mu + phi))
        step = (score - (eta - m) / r) / (info + 1.0 / r)
        step = max(-3.0, min(3.0, step))
        eta += step
        if abs(step) < 1e-10:
            break
    a = scale * math.exp(eta)
    mu = a + c
    info = a * a * phi / (mu * (mu + phi))
    if zero_mix:
        p0 = math.exp(phi * math.log(phi / (phi + mu)))
        info *= (1.0 - omega) * p0 / (omega + (1.0 - omega) * p0)
    return eta, 1.0 / (info + 1.0 / r)


# --- prepared data ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Prepared:
    days: tuple[float, ...]
    f_start: tuple[float, ...]
    net: tuple[float, ...]
    gains: tuple[float | None, ...]  # observed and usable for the gain likelihood
    x: tuple[float, ...]  # funnel covariate per interval (imputed where missing)
    losses: tuple[float | None, ...]
    level: Level
    pc_per_1k: float
    negative_losses: int
    imputed_x: int


def _choose_level(intervals: Sequence[RunInterval]) -> Level:
    has_pc = [finite(iv.profile_clicks) for iv in intervals]
    has_imp = [finite(iv.impressions) for iv in intervals]
    if any(has_pc) and all(p or not i for p, i in zip(has_pc, has_imp, strict=True)):
        return "L2"
    if any(has_imp):
        return "L3"
    return "L0"


def _prepare(intervals: Sequence[RunInterval]) -> _Prepared:
    level = _choose_level(intervals)

    def raw_x(iv: RunInterval) -> float | None:
        if level == "L2":
            return iv.profile_clicks if finite(iv.profile_clicks) else None
        if level == "L3":
            return iv.impressions / 1000.0 if iv.impressions is not None and finite(iv.impressions) else None
        return 0.0

    xs = [raw_x(iv) for iv in intervals]
    known = [(x, iv.days) for x, iv in zip(xs, intervals, strict=True) if x is not None]
    per_day = sum(x for x, _ in known) / max(sum(d for _, d in known), 1e-9) if known else 0.0
    x_filled = tuple(x if x is not None else per_day * iv.days for x, iv in zip(xs, intervals, strict=True))

    gains: list[float | None] = []
    losses: list[float | None] = []
    negative = 0
    for iv in intervals:
        if iv.gains_observed:
            assert iv.gains is not None
            gains.append(float(iv.gains))
            loss = iv.gains - iv.net_change
            if loss < 0:
                negative += 1
                loss = 0
            losses.append(float(loss))
        else:
            gains.append(None)
            losses.append(None)

    pcs = [
        (iv.profile_clicks, iv.impressions)
        for iv in intervals
        if finite(iv.profile_clicks) and finite(iv.impressions)
    ]
    imp_total = sum(i for _, i in pcs if i is not None)
    pc_total = sum(p for p, _ in pcs if p is not None)
    pc_per_1k = 1000.0 * pc_total / imp_total if imp_total > 0 else DEFAULT_PC_PER_1K
    return _Prepared(
        days=tuple(iv.days for iv in intervals),
        f_start=tuple(float(iv.followers_start) for iv in intervals),
        net=tuple(float(iv.net_change) for iv in intervals),
        gains=tuple(gains),
        x=x_filled,
        losses=tuple(losses),
        level=level,
        pc_per_1k=pc_per_1k,
        negative_losses=negative,
        imputed_x=sum(1 for x in xs if x is None),
    )


# --- filters ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _LossPass:
    loglik: float
    m: float
    c: float
    pred: tuple[tuple[float, float], ...]  # prior predictive (mean, var) of L_k per interval


def _loss_pass(p: _Prepared, phi: float, prior: NormalPrior, delta: float) -> _LossPass:
    m, c = prior.mean, prior.sd**2
    ll = 0.0
    burned = False
    pred: list[tuple[float, float]] = []
    for d, f, y in zip(p.days, p.f_start, p.losses, strict=True):
        r = c / delta**d
        scale = max(f, 1.0) * d
        mean_mu, var_mu = _lognormal_moments(m, r, scale)
        mean, phi_eff, var = _moment_nb(mean_mu, var_mu, phi)
        pred.append((mean, var))
        if y is None:
            c = r
            continue
        if burned:
            ll += nb_logpmf(y, mean, phi_eff)
        burned = True
        m, c = _laplace_update(m, r, y, scale, 0.0, phi, 0.0)
    return _LossPass(ll, m, c, tuple(pred))


@dataclass(frozen=True)
class _StepRecord:
    index: int
    observed: float | None
    mean: float
    phi: float
    omega: float
    p_zero: float


@dataclass(frozen=True)
class _GainPass:
    loglik: float
    m: float
    c: float
    records: tuple[_StepRecord, ...]


def _gain_pass(
    p: _Prepared,
    pi: float,
    phi: float,
    omega: float,
    prior: NormalPrior,
    delta: float,
    loss_pred: Sequence[tuple[float, float]],
    record: bool = False,
) -> _GainPass:
    m, c = prior.mean, prior.sd**2
    ll = 0.0
    burned = False
    records: list[_StepRecord] = []
    for k, (d, x, y, net) in enumerate(zip(p.days, p.x, p.gains, p.net, strict=True)):
        r = c / delta**d
        conv = pi * x
        mean_o, var_o = _lognormal_moments(m, r, d)
        mean, phi_eff, _ = _moment_nb(mean_o + conv, var_o, phi)
        if y is not None:
            if burned:
                ll += _zinb_logpmf(y, mean, phi_eff, omega)
            burned = True
            if record:
                p0 = omega + (1.0 - omega) * math.exp(nb_logpmf(0.0, mean, phi_eff))
                records.append(_StepRecord(k, y, mean, phi_eff, omega, p0))
            m, c = _laplace_update(m, r, y, d, conv, phi, omega)
            continue
        # gains missing or incomplete: an extended Kalman step on ΔF + E[L]
        loss_mean, loss_var = loss_pred[k]
        a = d * math.exp(m)
        mu = (1.0 - omega) * (a + conv)
        obs_var = mu + mu * mu / phi + loss_var
        h = (1.0 - omega) * a
        s = h * h * r + obs_var
        innov = net + loss_mean - mu
        if burned:
            ll += -0.5 * (math.log(2.0 * math.pi * s) + innov * innov / s)
        gain = r * h / s
        m = m + gain * innov
        c = max(r * (1.0 - gain * h), 1e-10)
        if record:
            records.append(_StepRecord(k, None, mean, phi_eff, omega, float("nan")))
    return _GainPass(ll, m, c, tuple(records))


# --- fit ------------------------------------------------------------------------------------------------


def _pi_prior(level: Level, priors: FollowerPriors) -> NormalPrior:
    return priors.logit_pi_pc if level == "L2" else priors.log_pi_imp


def pi_from_t(t: float, level: str) -> float:
    if level == "L2":
        return float(special.expit(t))
    if level == "L3":
        return math.exp(t)
    return 0.0


def _dpi_dt(t: float, level: str) -> float:
    if level == "L2":
        s = float(special.expit(t))
        return s * (1.0 - s)
    if level == "L3":
        return math.exp(t)
    return 0.0


def _hessian(f: Any, x: np.ndarray, h: float = 1e-3) -> np.ndarray:
    n = x.size
    hess = np.zeros((n, n))
    f0 = f(x)
    for i in range(n):
        ei = np.zeros(n)
        ei[i] = h
        hess[i, i] = (f(x + ei) - 2.0 * f0 + f(x - ei)) / (h * h)
        for j in range(i + 1, n):
            ej = np.zeros(n)
            ej[j] = h
            v = (f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)) / (4.0 * h * h)
            hess[i, j] = hess[j, i] = v
    return hess


def _laplace_cov(hess: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    try:
        cov = np.linalg.inv(hess)
        np.linalg.cholesky(cov)
        return np.asarray(cov)
    except np.linalg.LinAlgError:
        return fallback


@dataclass(frozen=True)
class IntervalDiagnostic:
    """One-step-ahead predictive for an interval's gains (plug-in static parameters)."""

    end: str
    days: float
    observed: int | None
    mean: float
    phi: float
    omega: float
    pit_rand: float | None
    z: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "IntervalDiagnostic":
        return cls(**d)


@dataclass(frozen=True)
class GainPredictive:
    """Predictive NB2 (optionally zero-inflated) for the gains of one interval."""

    mean: float
    phi: float
    omega: float = 0.0

    @property
    def variance(self) -> float:
        m = self.mean
        base_var = m + m * m / self.phi
        return (1.0 - self.omega) * (base_var + m * m) - ((1.0 - self.omega) * m) ** 2

    def cdf(self, y: float) -> float:
        if y < 0:
            return 0.0
        p = self.phi / (self.phi + self.mean)
        return self.omega + (1.0 - self.omega) * float(stats.nbinom.cdf(math.floor(y), self.phi, p))

    def pmf(self, y: int) -> float:
        base = math.exp(nb_logpmf(float(y), self.mean, self.phi))
        return (self.omega if y == 0 else 0.0) + (1.0 - self.omega) * base

    def randomized_pit(self, y: int, seed_id: str) -> float:
        """u = F(y-1) + V·p(y), V ~ U(0,1) seeded by ``seed_id`` (MODEL §4.1: monitors only)."""
        v = float(np.random.default_rng(_seed_from(seed_id)).uniform())
        return self.cdf(y - 1) + v * self.pmf(y)

    def z(self, y: int, seed_id: str) -> float:
        """Φ⁻¹ of the randomized PIT: the follower-gain drift detector's input (MODEL §4.3)."""
        u = min(max(self.randomized_pit(y, seed_id), 1e-12), 1.0 - 1e-12)
        return float(special.ndtri(u))

    def to_dict(self) -> dict[str, float]:
        return {"mean": self.mean, "phi": self.phi, "omega": self.omega}


def _seed_from(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode()).digest()[:8], "big")


@dataclass(frozen=True)
class FollowerFit:
    """A fitted follower model. JSON round-trip via ``to_dict``/``from_dict``.

    ``theta`` holds the static parameters on their unconstrained scale (names in ``theta_names``);
    ``theta_cov`` is their Laplace covariance (prior covariance when ``label == "prior_only"``).
    ``eta_*`` is the filtered organic log level per day at ``last_end``, conditional on ``theta``;
    ``eta_sens`` is d(eta_mean)/d(theta).
    """

    version: str
    label: FitLabel
    level: Level
    theta_names: tuple[str, ...]
    theta: tuple[float, ...]
    theta_cov: tuple[tuple[float, ...], ...]
    eta_mean: float
    eta_var: float
    eta_sens: tuple[float, ...]
    log_rho_mean: float
    log_rho_var: float
    zero_inflated: bool
    pc_per_1k: float
    mean_interval_days: float
    last_end: datetime | None
    followers_last: int | None
    n_intervals: int
    n_gain_obs: int
    n_loss_obs: int
    n_negative_losses: int
    n_imputed_x: int
    span_days: float
    log_posterior: float
    config: FollowerConfig = field(default_factory=FollowerConfig)
    priors: FollowerPriors = field(default_factory=FollowerPriors)
    diagnostics: tuple[IntervalDiagnostic, ...] = ()

    def _get(self, name: str) -> float:
        return self.theta[self.theta_names.index(name)] if name in self.theta_names else float("nan")

    @property
    def pi(self) -> float:
        """Follows per unit of X: per profile click (L2) or per 1,000 impressions (L3)."""
        return pi_from_t(self._get("t_pi"), self.level)

    @property
    def phi_gain(self) -> float:
        return math.exp(self._get("log_phi_gain"))

    @property
    def phi_loss(self) -> float:
        return math.exp(self._get("log_phi_loss"))

    @property
    def omega(self) -> float:
        return float(special.expit(self._get("logit_omega"))) if self.zero_inflated else 0.0

    @property
    def organic_per_day(self) -> float:
        return math.exp(self.eta_mean)

    @property
    def loss_rate_per_day(self) -> float:
        return math.exp(self.log_rho_mean)

    def theta_sd(self, name: str) -> float:
        i = self.theta_names.index(name)
        return math.sqrt(self.theta_cov[i][i])

    def interval(self, name: str, level: float = 0.9) -> tuple[float, float]:
        """Central Laplace interval for a static parameter, on its unconstrained scale."""
        zq = float(special.ndtri(0.5 + level / 2.0))
        mid, sd = self._get(name), self.theta_sd(name)
        return mid - zq * sd, mid + zq * sd

    def one_step_predictive(self, days: float, x: float | None = None) -> GainPredictive:
        """Predictive for the gains of the next interval of ``days`` days with covariate ``x``.

        ``x`` is that interval's profile clicks (L2) or impressions / 1000 (L3); the drift detector
        passes the observed value once the interval has happened. Includes the filtered level's
        uncertainty and, by the delta method, π's (with its correlation to the level).
        """
        r = self.eta_var / self.config.gain_discount**days
        xv = 0.0 if x is None or self.level == "L0" else float(x)
        t = self._get("t_pi")
        conv = self.pi * xv
        mean_o, var_o = _lognormal_moments(self.eta_mean, r, days)
        if "t_pi" in self.theta_names and self.level != "L0":
            i = self.theta_names.index("t_pi")
            grad = mean_o * self.eta_sens[i] + xv * _dpi_dt(t, self.level)
            var_o += grad * grad * self.theta_cov[i][i]
        mean, phi_eff, _ = _moment_nb(mean_o + conv, var_o, self.phi_gain)
        return GainPredictive(mean, phi_eff, self.omega)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "label": self.label,
            "level": self.level,
            "pi": self.pi,
            "phi_gain": self.phi_gain,
            "phi_loss": self.phi_loss,
            "organic_per_day": self.organic_per_day,
            "loss_rate_per_day": self.loss_rate_per_day,
            "zero_inflated": self.zero_inflated,
        }
        if self.zero_inflated:
            out["omega"] = self.omega
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "label": self.label,
            "level": self.level,
            "theta_names": list(self.theta_names),
            "theta": list(self.theta),
            "theta_cov": [list(r) for r in self.theta_cov],
            "eta_mean": self.eta_mean,
            "eta_var": self.eta_var,
            "eta_sens": list(self.eta_sens),
            "log_rho_mean": self.log_rho_mean,
            "log_rho_var": self.log_rho_var,
            "zero_inflated": self.zero_inflated,
            "pc_per_1k": self.pc_per_1k,
            "mean_interval_days": self.mean_interval_days,
            "last_end": None if self.last_end is None else self.last_end.isoformat(),
            "followers_last": self.followers_last,
            "n_intervals": self.n_intervals,
            "n_gain_obs": self.n_gain_obs,
            "n_loss_obs": self.n_loss_obs,
            "n_negative_losses": self.n_negative_losses,
            "n_imputed_x": self.n_imputed_x,
            "span_days": self.span_days,
            "log_posterior": self.log_posterior,
            "config": self.config.to_dict(),
            "priors": self.priors.to_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FollowerFit":
        return cls(
            version=d["version"],
            label=d["label"],
            level=d["level"],
            theta_names=tuple(d["theta_names"]),
            theta=tuple(float(v) for v in d["theta"]),
            theta_cov=tuple(tuple(float(v) for v in r) for r in d["theta_cov"]),
            eta_mean=float(d["eta_mean"]),
            eta_var=float(d["eta_var"]),
            eta_sens=tuple(float(v) for v in d["eta_sens"]),
            log_rho_mean=float(d["log_rho_mean"]),
            log_rho_var=float(d["log_rho_var"]),
            zero_inflated=bool(d["zero_inflated"]),
            pc_per_1k=float(d["pc_per_1k"]),
            mean_interval_days=float(d["mean_interval_days"]),
            last_end=None if d.get("last_end") is None else datetime.fromisoformat(d["last_end"]),
            followers_last=None if d.get("followers_last") is None else int(d["followers_last"]),
            n_intervals=int(d["n_intervals"]),
            n_gain_obs=int(d["n_gain_obs"]),
            n_loss_obs=int(d["n_loss_obs"]),
            n_negative_losses=int(d["n_negative_losses"]),
            n_imputed_x=int(d["n_imputed_x"]),
            span_days=float(d["span_days"]),
            log_posterior=float(d["log_posterior"]),
            config=FollowerConfig.from_dict(d["config"]),
            priors=FollowerPriors.from_dict(d["priors"]),
            diagnostics=tuple(IntervalDiagnostic.from_dict(x) for x in d.get("diagnostics", [])),
        )


def _zero_share(gains: Sequence[float | None]) -> float:
    obs = [g for g in gains if g is not None]
    return sum(1 for g in obs if g == 0) / len(obs) if obs else 0.0


def _log_evidence(log_post: float, cov: np.ndarray, prior_sd: np.ndarray) -> float:
    """Laplace log marginal likelihood from the unnormalised log posterior at the MAP."""
    k = int(cov.shape[0])
    sign, logdet = np.linalg.slogdet(cov)
    if sign <= 0:
        return -math.inf
    norm = -float(np.sum(np.log(prior_sd * math.sqrt(2.0 * math.pi))))
    return log_post + norm + 0.5 * k * math.log(2.0 * math.pi) + 0.5 * float(logdet)


def fit_follower_model(
    intervals: Sequence[RunInterval],
    priors: FollowerPriors | None = None,
    config: FollowerConfig | None = None,
) -> FollowerFit:
    """Fit the follower DGLM to run intervals (oldest first or any order; sorted by start)."""
    priors = priors or FollowerPriors()
    config = config or FollowerConfig()
    ivs = sorted(intervals, key=lambda iv: iv.start)
    p = _prepare(ivs)
    span = (ivs[-1].end - ivs[0].start).total_seconds() / 86_400.0 if ivs else 0.0
    label: FitLabel = "fitted" if span >= config.prior_only_days else "prior_only"
    pi_prior = _pi_prior(p.level, priors)

    # losses first: they depend on φ_L only
    lp_loss = priors.log_phi_loss
    if label == "fitted" and any(v is not None for v in p.losses):

        def loss_obj(v: float) -> float:
            lp = _loss_pass(p, math.exp(v), priors.log_rho0, config.loss_discount)
            return -(lp.loglik - 0.5 * ((v - lp_loss.mean) / lp_loss.sd) ** 2)

        res = optimize.minimize_scalar(
            loss_obj, bounds=(lp_loss.mean - 5 * lp_loss.sd, lp_loss.mean + 5 * lp_loss.sd), method="bounded"
        )
        log_phi_l = float(res.x)
        h = 1e-3
        curv = (loss_obj(log_phi_l + h) - 2 * loss_obj(log_phi_l) + loss_obj(log_phi_l - h)) / (h * h)
        var_phi_l = 1.0 / curv if curv > 0 else lp_loss.sd**2
        loss_post = -float(res.fun)
    else:
        log_phi_l, var_phi_l, loss_post = lp_loss.mean, lp_loss.sd**2, 0.0
    loss_fit = _loss_pass(p, math.exp(log_phi_l), priors.log_rho0, config.loss_discount)

    def run_gain(theta: np.ndarray, zi: bool, record: bool = False) -> _GainPass:
        omega = float(special.expit(theta[2])) if zi else 0.0
        return _gain_pass(
            p,
            pi_from_t(float(theta[0]), p.level),
            math.exp(float(theta[1])),
            omega,
            priors.eta0,
            config.gain_discount,
            loss_fit.pred,
            record,
        )

    def prior_vec(zi: bool) -> tuple[np.ndarray, np.ndarray]:
        ps = [pi_prior, priors.log_phi_gain] + ([priors.logit_omega] if zi else [])
        return np.array([q.mean for q in ps]), np.array([q.sd for q in ps])

    def neg_post(theta: np.ndarray, zi: bool) -> float:
        mean, sd = prior_vec(zi)
        lp = -0.5 * float(np.sum(((theta - mean) / sd) ** 2))
        if p.level == "L0":  # π doesn't enter the likelihood; keep it at its prior
            lp = -0.5 * float(np.sum(((theta[1:] - mean[1:]) / sd[1:]) ** 2))
        return -(run_gain(theta, zi).loglik + lp)

    def map_fit(zi: bool) -> tuple[np.ndarray, np.ndarray, float]:
        mean, sd = prior_vec(zi)
        if label == "prior_only" or not any(g is not None for g in p.gains):
            return mean, np.diag(sd**2), 0.0
        starts = [mean.copy()]
        for shift in (-1.0, 1.0):
            s = mean.copy()
            s[0] += shift * sd[0]
            starts.append(s)
        best: Any = None
        for x0 in starts:
            res = optimize.minimize(
                neg_post,
                x0,
                args=(zi,),
                method="Nelder-Mead",
                options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000},
            )
            if best is None or res.fun < best.fun:
                best = res
        x = np.asarray(best.x, dtype=float)
        if p.level == "L0":
            x[0] = mean[0]
        hess = _hessian(lambda v: neg_post(v, zi), x)
        if p.level == "L0":
            hess[0, :] = 0.0
            hess[:, 0] = 0.0
            hess[0, 0] = 1.0 / sd[0] ** 2
        return x, _laplace_cov(hess, np.diag(sd**2)), -float(best.fun)

    zi = False
    theta, cov, gain_post = map_fit(zi)
    if label == "fitted" and _zero_share(p.gains) > config.zero_share:
        theta_zi, cov_zi, post_zi = map_fit(True)
        gain_ev = _log_evidence(gain_post, cov, prior_vec(False)[1])
        zi_ev = _log_evidence(post_zi, cov_zi, prior_vec(True)[1])
        if zi_ev - gain_ev > config.zero_log_bayes_factor:
            zi, theta, cov, gain_post = True, theta_zi, cov_zi, post_zi
    final = run_gain(theta, zi, record=True)

    # sensitivity of the filtered level to the static parameters (central differences)
    sens = np.zeros(theta.size)
    for i in range(theta.size):
        e = np.zeros(theta.size)
        e[i] = 1e-4
        sens[i] = (run_gain(theta + e, zi).m - run_gain(theta - e, zi).m) / 2e-4

    names = ["t_pi", "log_phi_gain"] + (["logit_omega"] if zi else []) + ["log_phi_loss"]
    full = np.zeros((len(names), len(names)))
    k = theta.size
    full[:k, :k] = cov
    full[k, k] = var_phi_l
    diags = _diagnostics(ivs, final.records)
    return FollowerFit(
        version=MODEL_VERSION,
        label=label,
        level=p.level,
        theta_names=tuple(names),
        theta=(*(float(v) for v in theta), log_phi_l),
        theta_cov=tuple(tuple(float(v) for v in row) for row in full),
        eta_mean=final.m,
        eta_var=final.c,
        eta_sens=(*(float(v) for v in sens), 0.0),
        log_rho_mean=loss_fit.m,
        log_rho_var=loss_fit.c,
        zero_inflated=zi,
        pc_per_1k=p.pc_per_1k,
        mean_interval_days=float(np.mean(p.days)) if p.days else 2.0,
        last_end=ivs[-1].end if ivs else None,
        followers_last=ivs[-1].followers_end if ivs else None,
        n_intervals=len(ivs),
        n_gain_obs=sum(1 for g in p.gains if g is not None),
        n_loss_obs=sum(1 for v in p.losses if v is not None),
        n_negative_losses=p.negative_losses,
        n_imputed_x=p.imputed_x,
        span_days=span,
        log_posterior=gain_post + loss_post,
        config=config,
        priors=priors,
        diagnostics=diags,
    )


def _diagnostics(
    ivs: Sequence[RunInterval], records: Sequence[_StepRecord]
) -> tuple[IntervalDiagnostic, ...]:
    out = []
    for rec in records:
        iv = ivs[rec.index]
        seed_id = f"followers:{iv.end.isoformat()}"
        if rec.observed is None:
            out.append(
                IntervalDiagnostic(
                    iv.end.isoformat(), iv.days, None, rec.mean, rec.phi, rec.omega, None, None
                )
            )
            continue
        pred = GainPredictive(rec.mean, rec.phi, rec.omega)
        y = int(rec.observed)
        out.append(
            IntervalDiagnostic(
                iv.end.isoformat(),
                iv.days,
                y,
                rec.mean,
                rec.phi,
                rec.omega,
                pred.randomized_pit(y, seed_id),
                pred.z(y, seed_id),
            )
        )
    return tuple(out)
