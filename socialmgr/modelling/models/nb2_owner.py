"""Owner-level NB2 reach model (MODEL.md §2a; IMPLEMENTATION_PLAN §6 v1 replacement row).

Same likelihood as the prior-only model in ``nb2.py``:

    I ~ NB2(mu, phi),  q ~ N(0, 1)
    mu = (m_in(type) F^beta exp(a_in) + m_oon(type) exp(a_oon + theta_url has_url)) exp(lambda q)
    m_in = 1 (original/quote) or 0.75 rho_th (replies),  m_oon = 1 or 0

Below ``MIN_OWNER_POSTS`` matured owner posts ``fit`` returns exactly the prior-only model (same
draws, same version string), so forecasts frozen before the switch still recompute. From 20 posts
the owner's matured outcomes give a joint posterior over (a_in, a_oon, log phi, beta, lambda,
rho_th), plus theta_url when the training examples carry a ``has_url`` flag that varies. Their §2a
priors are the priors of that posterior: beta, lambda and rho_th are included because they cost
nothing extra, but with one account they are only weakly identified (beta needs follower growth,
rho_th needs replies, and lambda trades off against phi because q only adds log-normal
over-dispersion to a reach-only model), so their posteriors stay close to the priors.

The latent quality q is integrated out of each post's likelihood by adaptive Gauss-Hermite
quadrature: nodes are centred on the mode of q's conditional posterior (a Gaussian approximation
with the NB2 term's variance in log mu taken as 1/(y + ½) + 1/phi, then two Newton steps) and
scaled by its curvature there, so 9 nodes stay accurate whether the NB2 term is broad or sharp.

The sampler works in coordinates in which the posterior is close to a product of independent
pieces: the total level of an original at the reference follower count, and the gap to a reply's
in-network level with how that gap splits between the in-network share and rho_th (instead of a_in,
a_oon and rho_th, of which originals identify only the sum of the two components and replies only
a_in + log rho_th); and the total log-scale over-dispersion s^2 = lambda^2 + 1/phi with the ratio
lambda sqrt(phi) (instead of lambda and phi). The likelihood depends on lambda only through lambda^2
(q is symmetric), so lambda is sampled with a sign under its half-normal prior folded onto both signs,
which removes the boundary at lambda = 0; |lambda| is reported. The Jacobians are written out in
``_natural`` and ``_log_posterior``.

Fits are cached in process by a hash of (owner training examples, prior, seed, sampler settings):
many forecasts in one run share a training set.
"""

import hashlib
import json
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy import optimize, special

from socialmgr.core.interfaces import FittedModel
from socialmgr.core.types import Distribution, ForecastSubject, PredictiveDraws, PriorSpec, TrainingExample
from socialmgr.modelling import mcmc
from socialmgr.modelling.models.nb2 import N_DRAWS, REPLY_TYPES, NB2PriorModel

FloatArray = NDArray[np.float64]

MIN_OWNER_POSTS = 20  # MODEL §2a: at least 20 owner posts before owner-level terms
# 0.2 (2026-11-26): sampling continues, with a proposal re-centred on the draws so far and twice as
# many draws, until R-hat and bulk ESS meet the release gate's bar (at most two extra rounds). Found
# on a real account whose replies reach ~3% of an original (rho_th near 0), where one round mixed slowly.
OWNER_VERSION = "owner-0.2"
CONVERGED_RHAT = 1.008  # a margin under the gate's 1.01
CONVERGED_ESS = 500.0  # and over its 400
MAX_EXTRA_ROUNDS = 2
GH_NODES = 9
NEWTON_STEPS = 2
FIT_TYPES = frozenset({"original", "quote", "reply", "self_reply"})
CACHE_SIZE = 128
_LOG_075 = math.log(0.75)

_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(GH_NODES)
_GH_LOGW = np.log(_GH_W / math.sqrt(2 * math.pi)) + 0.5 * _GH_X**2  # weight / N(x; 0, 1)


def _softplus(x: FloatArray) -> FloatArray:
    return np.asarray(np.logaddexp(0.0, x), dtype=np.float64)


def _require(prior: PriorSpec, name: str, *families: str) -> Distribution:
    dist = prior.params.get(name)
    if dist is None or dist.family not in families:
        raise ValueError(f"nb2_owner needs prior {name!r} with family in {families}, got {dist}")
    return dist


def _normal_logpdf(dist: Distribution, x: FloatArray) -> FloatArray:
    p = dist.params
    return np.asarray(-0.5 * ((x - p["mean"]) / p["sd"]) ** 2, dtype=np.float64)


@dataclass(frozen=True)
class _Priors:
    a_in: Distribution
    a_oon: Distribution
    beta: Distribution
    log_phi: Distribution
    lam: Distribution
    rho: Distribution
    theta_url: Distribution

    @classmethod
    def from_spec(cls, prior: PriorSpec) -> "_Priors":
        return cls(
            a_in=_require(prior, "log_in_rate", "normal"),
            a_oon=_require(prior, "log_i48_at_zero", "normal"),
            beta=_require(prior, "beta", "normal"),
            log_phi=_require(prior, "log_phi", "normal"),
            lam=_require(prior, "lambda", "half_normal"),
            rho=_require(prior, "rho_th", "beta"),
            theta_url=_require(prior, "theta_url", "normal"),
        )


@dataclass(frozen=True)
class _Data:
    y: FloatArray
    log_f: FloatArray  # log followers, 0 where followers == 0 (masked by has_f)
    has_f: NDArray[np.bool_]
    is_reply: NDArray[np.bool_]
    has_url: FloatArray
    log_f_ref: float
    with_url: bool  # theta_url is a sampled parameter

    @classmethod
    def from_examples(cls, examples: Sequence[TrainingExample]) -> "_Data":
        y = np.array([max(round(e.outcome_i48), 0) for e in examples], dtype=np.float64)
        followers = np.array([max(e.followers_at_post, 0) for e in examples], dtype=np.float64)
        has_f = followers > 0
        log_f = np.where(has_f, np.log(np.maximum(followers, 1.0)), 0.0)
        url = np.array([bool(getattr(e, "has_url", False)) for e in examples], dtype=np.float64)
        f_ref = float(np.median(followers[has_f])) if has_f.any() else 1.0
        return cls(
            y=y,
            log_f=log_f,
            has_f=has_f,
            is_reply=np.array([e.type in REPLY_TYPES for e in examples]),
            has_url=url,
            log_f_ref=math.log(max(f_ref, 1.0)),
            with_url=bool(0 < url.sum() < url.size),
        )


def _param_names(with_url: bool) -> tuple[str, ...]:
    base = ("level", "log_gap", "logit_split", "beta", "log_s", "ratio")
    return (*base, "theta_url") if with_url else base


def _natural(u: FloatArray, data: _Data) -> dict[str, FloatArray]:
    """Unconstrained (chains, d) -> natural parameters, each (chains,).

    level L = log(in + oon) of an original at the reference follower count. With A = L - log(in)
    and B = -log rho_th, a reply's in-network level is L - (A + B): replies identify the gap A + B
    and originals identify L, so the sampler uses (L, log(A + B), logit(A / (A + B))), in which the
    weakly identified direction (how the gap splits between A and B) is a straight line.
    """
    level, log_gap, logit_split, beta, log_s, ratio = (u[:, i] for i in range(6))
    gap = np.exp(log_gap)
    log_p = -_softplus(-logit_split)
    log_1mp = -_softplus(logit_split)
    a = gap * np.exp(log_p)
    b = gap * np.exp(log_1mp)
    log_1m_ea = np.log(-np.expm1(-a))
    out = {
        "level": level,
        "a_in": level - a - beta * data.log_f_ref,
        "a_oon": level + log_1m_ea,
        "beta": beta,
        "log_phi": -2.0 * log_s + np.log1p(ratio * ratio),
        "lambda_signed": ratio * np.exp(log_s) / np.sqrt(1.0 + ratio * ratio),
        # log |d(lambda, log phi) / d(log s, ratio)|, up to log 2
        "log_jac_disp": log_s - 0.5 * np.log1p(ratio * ratio),
        "log_rho": -b,
        "log_1m_rho": np.log(-np.expm1(-b)),
        # log |d(a_in, a_oon, log rho) / d(level, log_gap, logit_split)|
        "log_jac_level": -log_1m_ea + 2.0 * log_gap + log_p + log_1mp,
    }
    out["lambda"] = np.abs(out["lambda_signed"])
    out["rho_th"] = np.exp(out["log_rho"])
    if data.with_url:
        out["theta_url"] = u[:, 6]
    return out


def _log_mu(nat: dict[str, FloatArray], data: _Data) -> FloatArray:
    """log mu without q, (chains, n)."""
    log_in = nat["a_in"][:, None] + nat["beta"][:, None] * data.log_f[None, :]
    log_in = log_in + np.where(data.is_reply[None, :], _LOG_075 + nat["log_rho"][:, None], 0.0)
    log_in = np.where(data.has_f[None, :], log_in, -np.inf)
    url = nat["theta_url"][:, None] * data.has_url[None, :] if data.with_url else 0.0
    log_oon = np.where(data.is_reply[None, :], -np.inf, nat["a_oon"][:, None] + url)
    return np.maximum(np.logaddexp(log_in, log_oon), math.log(1e-9))


def log_lik_points(
    m: FloatArray, lam: FloatArray, log_phi: FloatArray, y: FloatArray, nodes: int = GH_NODES
) -> FloatArray:
    """Per-post log NB2 likelihood with q integrated out (adaptive Gauss-Hermite), up to the
    constant -log Γ(y + 1). Shapes: m (c, n), lam and log_phi (c,), y (n,). Returns (c, n)."""
    if nodes == GH_NODES:
        x, logw = _GH_X, _GH_LOGW
    else:
        x, w = np.polynomial.hermite_e.hermegauss(nodes)
        logw = np.log(w / math.sqrt(2 * math.pi)) + 0.5 * x**2
    phi = np.exp(log_phi)[:, None]
    lam2 = lam[:, None]
    v = 1.0 / (y[None, :] + 0.5) + 1.0 / phi
    prec = 1.0 + lam2 * lam2 / v
    q_mean = lam2 * (np.log(y[None, :] + 0.5) - m) / v / prec
    # Newton steps towards the mode of q's conditional posterior; the curvature there sets the scale.
    yc, phic, lphic = y[None, :], phi, log_phi[:, None]
    for _ in range(NEWTON_STEPS + 1):
        r = special.expit(m + lam2 * q_mean - lphic)  # mu / (phi + mu)
        curv = lam2 * lam2 * (yc + phic) * r * (1.0 - r) + 1.0
        grad = lam2 * (yc - (yc + phic) * r) - q_mean
        if _ == NEWTON_STEPS:
            break
        q_mean = q_mean + grad / curv
    q_sd = 1.0 / np.sqrt(curv)
    q = q_mean[..., None] + q_sd[..., None] * x
    ell = m[..., None] + lam2[..., None] * q
    yy = y[None, :, None]
    lphi = log_phi[:, None, None]
    nb = yy * ell - (yy + phi[..., None]) * np.logaddexp(lphi, ell)
    const = special.gammaln(y[None, :] + phi) - special.gammaln(phi) + phi * log_phi[:, None]
    terms = logw + nb - 0.5 * q * q
    top = terms.max(axis=2)
    ll = top + np.log(np.exp(terms - top[..., None]).sum(axis=2)) + np.log(q_sd) + const
    return np.asarray(ll, dtype=np.float64)


def _log_posterior(u: FloatArray, data: _Data, priors: _Priors) -> FloatArray:
    nat = _natural(u, data)
    m = _log_mu(nat, data)
    ll = log_lik_points(m, nat["lambda"], nat["log_phi"], data.y).sum(axis=1)
    lam_sd = priors.lam.params["sd"]
    rho_a, rho_b = priors.rho.params["a"], priors.rho.params["b"]
    lp = (
        _normal_logpdf(priors.a_in, nat["a_in"])
        + _normal_logpdf(priors.a_oon, nat["a_oon"])
        + _normal_logpdf(priors.beta, nat["beta"])
        + _normal_logpdf(priors.log_phi, nat["log_phi"])
        - 0.5 * (nat["lambda_signed"] / lam_sd) ** 2  # half-N(sd) folded onto the sign of lambda
        + nat["log_jac_disp"]
        + (rho_a - 1.0) * nat["log_rho"]
        + (rho_b - 1.0) * nat["log_1m_rho"]
        + nat["log_rho"]  # d rho / d log rho
        + nat["log_jac_level"]
    )
    if data.with_url:
        lp = lp + _normal_logpdf(priors.theta_url, nat["theta_url"])
    return np.asarray(ll + lp, dtype=np.float64)


def _start(data: _Data, priors: _Priors) -> FloatArray:
    originals = data.y[~data.is_reply]
    level = math.log1p(float(np.median(originals if originals.size else data.y)))
    beta = priors.beta.params["mean"]
    log_in = priors.a_in.params["mean"] + beta * data.log_f_ref
    a = math.log1p(math.exp(priors.a_oon.params["mean"] - log_in))  # L - log(in) at the prior means
    ra, rb = priors.rho.params["a"], priors.rho.params["b"]
    b = -math.log(ra / (ra + rb))
    lam = 0.674 * priors.lam.params["sd"]
    phi = math.exp(priors.log_phi.params["mean"])
    s2 = lam * lam + 1.0 / phi
    u = [level, math.log(a + b), math.log(a / b), beta, 0.5 * math.log(s2), lam * math.sqrt(phi)]
    if data.with_url:
        u.append(priors.theta_url.params["mean"])
    return np.array(u, dtype=np.float64)


def _hessian(fn: Callable[[FloatArray], float], x: FloatArray, h: float = 1e-3) -> FloatArray:
    d = x.size
    hess = np.empty((d, d))
    eye = np.eye(d) * h
    f0 = fn(x)
    for i in range(d):
        for j in range(i, d):
            if i == j:
                val = (fn(x + eye[i]) - 2 * f0 + fn(x - eye[i])) / (h * h)
            else:
                val = (
                    fn(x + eye[i] + eye[j])
                    - fn(x + eye[i] - eye[j])
                    - fn(x - eye[i] + eye[j])
                    + fn(x - eye[i] - eye[j])
                ) / (4 * h * h)
            hess[i, j] = hess[j, i] = val
    return hess


@dataclass(frozen=True)
class FitDiagnostics:
    """Sampler diagnostics for the release gate (MODEL §5 gate 6)."""

    rhat: dict[str, float]
    ess_bulk: dict[str, float]
    chains: int
    draws_per_chain: int
    accept_rw: float
    accept_ind: float
    n_posts: int
    posterior_mean: dict[str, float]
    posterior_sd: dict[str, float]
    attempts: int = 1  # sampling rounds: more when the first didn't meet the convergence bar

    @property
    def rhat_max(self) -> float:
        return max(self.rhat.values())

    @property
    def ess_bulk_min(self) -> float:
        return min(self.ess_bulk.values())


DIAGNOSED = ("level", "a_in", "a_oon", "beta", "log_phi", "lambda", "rho_th")


@dataclass(frozen=True)
class FittedOwnerNB2:
    version: str
    seed: int
    prior: PriorSpec
    owner_posts: int
    draws: dict[str, FloatArray]  # natural parameters, N_DRAWS each
    diagnostics: FitDiagnostics
    meta: dict[str, str] = field(default_factory=dict)

    def predict(self, subject: ForecastSubject) -> PredictiveDraws:
        subject_seed = int.from_bytes(hashlib.sha256(subject.subject_id.encode()).digest()[:4], "big")
        rng = np.random.default_rng([self.seed, subject_seed])
        d = self.draws
        n = N_DRAWS
        if "theta_url" in d:
            theta_url = d["theta_url"]
        else:
            tp = self.prior.params["theta_url"].params
            theta_url = rng.normal(tp["mean"], tp["sd"], n)
        q = rng.normal(0.0, 1.0, n)
        is_reply = subject.type in REPLY_TYPES
        followers = float(max(subject.followers_at_post, 0))
        m_in = 0.75 * d["rho_th"] if is_reply else np.ones(n)
        m_oon = 0.0 if is_reply else 1.0
        mu_in = m_in * np.power(followers, d["beta"]) * np.exp(d["a_in"]) if followers > 0 else np.zeros(n)
        mu_oon = m_oon * np.exp(d["a_oon"] + theta_url * float(subject.has_url))
        mu = np.maximum((mu_in + mu_oon) * np.exp(d["lambda"] * q), 1e-9)
        phi = np.exp(d["log_phi"])
        rate = rng.gamma(phi, mu / phi)
        samples = rng.poisson(rate).astype(np.float64)
        return PredictiveDraws(
            metric="impressions",
            horizon_h=48.0,
            samples=samples,
            meta={
                "uses_owner_data": "true",
                "owner_posts_seen": str(self.owner_posts),
                **self.meta,
            },
        )


_cache: OrderedDict[str, FittedOwnerNB2] = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cache_size() -> int:
    return len(_cache)


class NB2OwnerModel:
    """Prior-only NB2 below 20 matured owner posts; owner-level NB2 from then on."""

    family = "nb2"
    version = OWNER_VERSION

    def __init__(self, chains: int = 4, warmup: int = 800, draws: int = 1500) -> None:
        self.chains = chains
        self.warmup = warmup
        self.draws = draws

    def fitted_version(self, prior: PriorSpec) -> str:
        return f"{self.family}@{self.version}+{prior.source}@{prior.version}"

    def fit(self, examples: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedModel:
        owner = [e for e in examples if e.account_role == "owner"]
        if len(owner) < MIN_OWNER_POSTS:
            return NB2PriorModel().fit(examples, prior, seed)
        return self.fit_owner(owner, prior, seed)

    def _key(self, owner: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> str:
        payload = {
            "version": self.fitted_version(prior),
            "sampler": [self.chains, self.warmup, self.draws, GH_NODES],
            "seed": seed,
            "prior": prior.model_dump(mode="json"),
            "training": [
                [
                    e.post_external_id,
                    e.created_at.isoformat(),
                    e.type,
                    e.followers_at_post,
                    e.outcome_i48,
                    bool(getattr(e, "has_url", False)),
                ]
                for e in owner
                if e.type in FIT_TYPES
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def fit_owner(self, owner: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedOwnerNB2:
        key = self._key(owner, prior, seed)
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                return hit
        fitted = self._fit(owner, prior, seed)
        with _cache_lock:
            _cache[key] = fitted
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
        return fitted

    def _fit(self, owner: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedOwnerNB2:
        used = [e for e in owner if e.type in FIT_TYPES]
        data = _Data.from_examples(used)
        priors = _Priors.from_spec(prior)
        names = _param_names(data.with_url)

        def logp(u: FloatArray) -> FloatArray:
            return _log_posterior(u, data, priors)

        def neg(u1: FloatArray) -> float:
            val = float(logp(u1[None, :])[0])
            return -val if math.isfinite(val) else 1e300

        start = _start(data, priors)
        opt = optimize.minimize(neg, start, method="BFGS", options={"maxiter": 200, "gtol": 1e-4})
        center = opt.x if np.all(np.isfinite(opt.x)) and opt.fun < neg(start) else start
        hess = _hessian(neg, center)
        vals, vecs = np.linalg.eigh(0.5 * (hess + hess.T))
        finite = bool(np.all(np.isfinite(vals)))
        cov = (vecs / np.clip(vals, 1e-2, None)) @ vecs.T if finite else np.eye(len(names))
        diag_names = (*DIAGNOSED, "theta_url") if data.with_url else DIAGNOSED
        draws_n, spread = self.draws, 2.0
        for attempt in range(MAX_EXTRA_ROUNDS + 1):
            rng = np.random.default_rng([seed, 0x0B2, attempt])
            chains = mcmc.sample(
                logp,
                center,
                cov,
                rng,
                chains=self.chains,
                warmup=self.warmup,
                draws=draws_n,
                init_spread=spread,
            )
            c, s, _ = chains.draws.shape
            nat_all = _natural(chains.pooled(), data)
            per_chain = {k: v.reshape(c, s) for k, v in nat_all.items()}
            rhat = {k: mcmc.rhat(per_chain[k]) for k in diag_names}
            ess = {k: mcmc.ess_bulk(per_chain[k]) for k in diag_names}
            if max(rhat.values()) < CONVERGED_RHAT and min(ess.values()) > CONVERGED_ESS:
                break
            # Not yet: re-centre the proposal on the draws (the Hessian at the mode understates a
            # posterior that leans on a boundary) and run longer.
            pooled = chains.pooled()
            center = pooled.mean(axis=0)
            cov = np.cov(pooled, rowvar=False) + 1e-9 * np.eye(pooled.shape[1])
            draws_n, spread = draws_n * 2, 1.0
        diagnostics = FitDiagnostics(
            rhat=rhat,
            ess_bulk=ess,
            chains=c,
            draws_per_chain=s,
            accept_rw=chains.accept_rw,
            accept_ind=chains.accept_ind,
            n_posts=len(used),
            posterior_mean={k: float(nat_all[k].mean()) for k in diag_names},
            posterior_sd={k: float(nat_all[k].std()) for k in diag_names},
            attempts=attempt + 1,
        )
        idx = np.linspace(0, c * s - 1, N_DRAWS).round().astype(int)
        keep = ("a_in", "a_oon", "beta", "log_phi", "lambda", "rho_th")
        draws = {k: nat_all[k][idx] for k in (*keep, "theta_url") if k in nat_all}
        return FittedOwnerNB2(
            version=self.fitted_version(prior),
            seed=seed,
            prior=prior,
            owner_posts=len(owner),
            draws=draws,
            diagnostics=diagnostics,
            meta={
                "model_variant": "owner",
                "owner_posts_used": str(len(used)),
                "rhat_max": f"{diagnostics.rhat_max:.4f}",
                "ess_bulk_min": f"{diagnostics.ess_bulk_min:.0f}",
            },
        )
