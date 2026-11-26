"""A small seeded multi-chain MCMC sampler and its convergence diagnostics (numpy only).

The sampler targets a log density on an unconstrained vector, vectorised over chains. Warmup runs
adaptive random-walk Metropolis (scale tuned to an acceptance of 0.234, covariance re-estimated from
the pooled warmup draws). Sampling then alternates one random-walk step with one independence step
whose proposal is a multivariate t fitted to the warmup draws and then frozen, so the chain is a
fixed mixture of two Metropolis-Hastings kernels and keeps the target as its stationary distribution.
On the near-Gaussian posteriors of the owner models the independence step accepts often, which gives
a high effective sample size for little compute; the random-walk step keeps the chain ergodic where
the proposal is poor.

Diagnostics follow Vehtari, Gelman, Simpson, Carpenter and Bürkner (2021): rank-normalised split R̂
(the larger of the bulk and folded versions) and bulk ESS with Geyer's initial monotone sequence.
Everything is deterministic given the seed.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import special

FloatArray = NDArray[np.float64]
LogDensity = Callable[[FloatArray], FloatArray]  # (chains, d) -> (chains,)

TARGET_ACCEPT = 0.234
T_DOF = 5.0  # independence proposal: multivariate t with this many degrees of freedom


@dataclass(frozen=True)
class Chains:
    draws: FloatArray  # (chains, draws, d)
    accept_rw: float
    accept_ind: float

    def pooled(self) -> FloatArray:
        """All draws, chain after chain: (chains * draws, d)."""
        c, n, d = self.draws.shape
        return self.draws.reshape(c * n, d)


def _safe(logp: LogDensity, x: FloatArray) -> FloatArray:
    out = np.asarray(logp(x), dtype=np.float64)
    return np.where(np.isfinite(out), out, -np.inf)


def _psd_cholesky(cov: FloatArray, floor: float = 1e-8) -> FloatArray:
    sym = 0.5 * (cov + cov.T)
    vals, vecs = np.linalg.eigh(sym)
    vals = np.maximum(vals, floor * max(float(vals.max()), 1.0))
    return np.linalg.cholesky((vecs * vals) @ vecs.T)


def _mvt_logpdf(x: FloatArray, loc: FloatArray, chol: FloatArray, dof: float) -> FloatArray:
    """Multivariate t log density up to a constant (the constant cancels in MH ratios)."""
    sol = np.linalg.solve(chol, (x - loc).T)  # (d, m)
    maha = np.sum(sol * sol, axis=0)
    d = x.shape[1]
    return np.asarray(-0.5 * (dof + d) * np.log1p(maha / dof), dtype=np.float64)


def _mvt_draw(rng: np.random.Generator, loc: FloatArray, chol: FloatArray, dof: float, m: int) -> FloatArray:
    d = loc.shape[0]
    z = rng.standard_normal((m, d))
    w = rng.chisquare(dof, m) / dof
    return np.asarray(loc + (z @ chol.T) / np.sqrt(w)[:, None], dtype=np.float64)


@dataclass
class _State:
    x: FloatArray
    lp: FloatArray
    step: float
    chol: FloatArray


def _rw_step(logp: LogDensity, st: _State, rng: np.random.Generator) -> NDArray[np.bool_]:
    chains, d = st.x.shape
    prop = st.x + st.step * (rng.standard_normal((chains, d)) @ st.chol.T)
    lp_prop = _safe(logp, prop)
    accept: NDArray[np.bool_] = np.log(rng.uniform(size=chains)) < lp_prop - st.lp
    st.x = np.where(accept[:, None], prop, st.x)
    st.lp = np.where(accept, lp_prop, st.lp)
    return accept


def _ind_step(
    logp: LogDensity, st: _State, rng: np.random.Generator, loc: FloatArray, chol: FloatArray
) -> NDArray[np.bool_]:
    chains = st.x.shape[0]
    prop = _mvt_draw(rng, loc, chol, T_DOF, chains)
    lp_prop = _safe(logp, prop)
    log_ratio = lp_prop - st.lp + _mvt_logpdf(st.x, loc, chol, T_DOF) - _mvt_logpdf(prop, loc, chol, T_DOF)
    accept: NDArray[np.bool_] = np.log(rng.uniform(size=chains)) < log_ratio
    st.x = np.where(accept[:, None], prop, st.x)
    st.lp = np.where(accept, lp_prop, st.lp)
    return accept


def sample(
    logp: LogDensity,
    center: FloatArray,
    cov: FloatArray,
    rng: np.random.Generator,
    chains: int = 4,
    warmup: int = 600,
    draws: int = 1000,
    init_spread: float = 1.0,
) -> Chains:
    """Run ``chains`` chains from dispersed starts around ``center`` (spread by ``cov``)."""
    d = center.shape[0]
    chol = _psd_cholesky(cov)
    x = center + init_spread * (rng.standard_normal((chains, d)) @ chol.T)
    lp = _safe(logp, x)
    # A start outside the support restarts at the centre.
    bad = ~np.isfinite(lp)
    if bad.any():
        x[bad] = center
        lp[bad] = _safe(logp, x[bad])

    state = _State(x=x, lp=lp, step=2.38 / math.sqrt(d), chol=chol)
    # Warmup, first half: random walk only, re-estimating the covariance at 25% and 50%.
    # Second half: random walk plus independence steps from a t fitted to the draws so far, refitted
    # at 75%. The independence steps mix fast, so the second-half covariance estimate is better.
    half = warmup // 2
    history = np.empty((warmup, chains, d))
    refits = {max(1, warmup // 4), max(1, half), max(1, (3 * warmup) // 4)}
    log_scale = math.log(state.step)
    proposal: tuple[FloatArray, FloatArray] | None = None
    for it in range(warmup):
        accept = _rw_step(logp, state, rng)
        if proposal is not None:
            _ind_step(logp, state, rng, *proposal)
        history[it] = state.x
        # Robbins-Monro on the global random-walk scale, decaying so it settles before sampling.
        log_scale += (float(accept.mean()) - TARGET_ACCEPT) / math.sqrt(it + 10.0)
        state.step = math.exp(log_scale)
        if it + 1 in refits:
            window = history[(it + 1) // 2 : it + 1].reshape(-1, d)
            if window.shape[0] > 2 * d:
                state.chol = _psd_cholesky(np.cov(window, rowvar=False) + 1e-9 * np.eye(d))
                log_scale = math.log(2.38 / math.sqrt(d))
                state.step = math.exp(log_scale)
                if it + 1 >= half:
                    proposal = (window.mean(axis=0), state.chol.copy())
    tail = history[half:].reshape(-1, d)
    if tail.shape[0] > 2 * d:
        proposal = (tail.mean(axis=0), _psd_cholesky(np.cov(tail, rowvar=False) + 1e-9 * np.eye(d)))
    elif proposal is None:
        proposal = (state.x.mean(axis=0), state.chol)

    # Sampling: a fixed mixture of the two kernels, nothing adapts.
    out = np.empty((chains, draws, d))
    acc_rw = acc_ind = 0
    for it in range(draws):
        acc_rw += int(_rw_step(logp, state, rng).sum())
        acc_ind += int(_ind_step(logp, state, rng, *proposal).sum())
        out[:, it] = state.x
    total = max(chains * draws, 1)
    return Chains(draws=out, accept_rw=acc_rw / total, accept_ind=acc_ind / total)


# ---------------------------------------------------------------- diagnostics


def _split(x: FloatArray) -> FloatArray:
    """(chains, n) -> (2 * chains, n // 2), dropping the middle draw when n is odd."""
    n = x.shape[1]
    half = n // 2
    return np.concatenate([x[:, :half], x[:, n - half :]], axis=0)


def _rank_normalize(x: FloatArray) -> FloatArray:
    flat = x.ravel()
    ranks = _average_ranks(flat)
    s = flat.size
    z = special.ndtri((ranks - 0.375) / (s + 0.25))
    return np.asarray(z.reshape(x.shape), dtype=np.float64)


def _average_ranks(v: FloatArray) -> FloatArray:
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(v.size, dtype=np.float64)
    ranks[order] = np.arange(1, v.size + 1, dtype=np.float64)
    # average ties
    sorted_v = v[order]
    _, first, counts = np.unique(sorted_v, return_index=True, return_counts=True)
    for start, count in zip(first, counts, strict=True):
        if count > 1:
            ranks[order[start : start + count]] = start + (count + 1) / 2.0
    return ranks


def _rhat_basic(x: FloatArray) -> float:
    m, n = x.shape
    if n < 2:
        return math.nan
    means = x.mean(axis=1)
    w = float(x.var(axis=1, ddof=1).mean())
    b = n * float(means.var(ddof=1)) if m > 1 else 0.0
    if w <= 0:
        return 1.0 if b == 0 else math.inf
    var_plus = (n - 1) / n * w + b / n
    return math.sqrt(var_plus / w)


def rhat(x: FloatArray) -> float:
    """Rank-normalised split R̂ for one parameter, x of shape (chains, draws)."""
    xs = _split(np.asarray(x, dtype=np.float64))
    bulk = _rhat_basic(_rank_normalize(xs))
    folded = np.abs(xs - np.median(xs))
    tail = _rhat_basic(_rank_normalize(folded))
    return max(bulk, tail)


def _autocov(x: FloatArray) -> FloatArray:
    """Biased autocovariance of each row via FFT."""
    n = x.shape[1]
    centred = x - x.mean(axis=1, keepdims=True)
    size = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfft(centred, n=size, axis=1)
    acov = np.fft.irfft(f * np.conj(f), n=size, axis=1)[:, :n]
    return np.asarray(acov.real / n, dtype=np.float64)


def _ess(x: FloatArray) -> float:
    m, n = x.shape
    if n < 4:
        return math.nan
    acov = _autocov(x)
    chain_mean = x.mean(axis=1)
    mean_var = float(acov[:, 0].mean()) * n / (n - 1.0)
    var_plus = mean_var * (n - 1.0) / n
    if m > 1:
        var_plus += float(chain_mean.var(ddof=1))
    if var_plus <= 0:
        return float(m * n)
    rho = np.zeros(n)
    rho_even = 1.0
    rho[0] = rho_even
    rho_odd = 1.0 - (mean_var - float(acov[:, 1].mean())) / var_plus
    rho[1] = rho_odd
    t = 1
    while t < n - 3 and rho_even + rho_odd > 0.0:
        rho_even = 1.0 - (mean_var - float(acov[:, t + 1].mean())) / var_plus
        rho_odd = 1.0 - (mean_var - float(acov[:, t + 2].mean())) / var_plus
        if rho_even + rho_odd >= 0:
            rho[t + 1] = rho_even
            rho[t + 2] = rho_odd
        t += 2
    max_t = t - 2
    if rho_even > 0:
        rho[max_t + 1] = rho_even
    t = 1
    while t <= max_t - 2:
        if rho[t + 1] + rho[t + 2] > rho[t - 1] + rho[t]:
            rho[t + 1] = (rho[t - 1] + rho[t]) / 2.0
            rho[t + 2] = rho[t + 1]
        t += 2
    total = m * n
    tau = -1.0 + 2.0 * float(rho[: max_t + 1].sum()) + float(rho[max_t + 1 : max_t + 2].sum())
    tau = max(tau, 1.0 / math.log10(total))
    return float(total / tau)


def ess_bulk(x: FloatArray) -> float:
    """Bulk effective sample size for one parameter, x of shape (chains, draws)."""
    return _ess(_rank_normalize(_split(np.asarray(x, dtype=np.float64))))
