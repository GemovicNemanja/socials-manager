"""Gibbs sampler for the MODEL §6.2 analysis model, batched over independent data sets.

    y_i = x_i·b + u_day(i) + e_i,   e_i ~ t(nu, 0, sigma),   u_d ~ N(0, s_day²)
    b ~ N(m0, P0^-1)  (Gaussian; carries beta ~ N(1, 0.25²), the tau_k family prior, gamma, lambda, slots)
    sigma ~ half-N(1.5),  nu ~ Gamma(2, rate 0.1),  s_day ~ half-N(day_sd_scale)

The Student-t is written as a scale mixture of normals, e_i | w_i ~ N(0, sigma²/w_i) with
w_i ~ Gamma(nu/2, rate nu/2), which makes every conditional cheap. One sweep (a partially
collapsed Gibbs sampler, van Dyk & Park 2008; the day effects are integrated out of the first two
steps and drawn right after them):

1. s_day | b, w, sigma        u integrated out (Woodbury per day); random-walk Metropolis
                              on log s_day
2. b | s_day, w, sigma        u integrated out; one joint Gaussian draw of all coefficients
3. u | b, s_day, w, sigma     per-day Gaussian
4. w | b, u, sigma, nu        Gamma
5. sigma² | b, u, w           independence Metropolis-Hastings: the inverse-gamma proposal matches
                              the likelihood exactly, so the acceptance ratio is the half-normal
                              prior ratio exp(-(s'² - s²)/(2·1.5²)) (acceptance ~0.8)
6. nu | w                     drawn on a fixed log-spaced grid (griddy Gibbs)

Integrating u out of the coefficient step removes the intercept/day-effect correlation that
makes a centred day random effect mix slowly. Every array carries a leading replicate axis R, so
the simulation tests run 1,000 experiments in one call; ``analyze`` calls it with R = 1. All
randomness comes from one seeded ``numpy.random.Generator``, so a call is reproducible bit for bit.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.special import gammaln

FloatArray = NDArray[np.float64]

SIGMA_SCALE = 1.5  # sigma ~ half-N(1.5)          (MODEL §6.2)
NU_SHAPE, NU_RATE = 2.0, 0.1  # nu ~ Gamma(2, 0.1)  (MODEL §6.2; rate parametrization, mean 20)
DAY_SD_SCALE = 0.5  # s_day ~ half-N(0.5): MODEL leaves it open; 0.5 is ICC ~0.3 at sigma ~0.9

_NU_GRID = np.geomspace(0.5, 150.0, 64)
_SDAY_STEP = 0.5  # random-walk step on log s_day


@dataclass(frozen=True)
class GibbsResult:
    draws: FloatArray  # (R, n_draws, len(keep)): kept coefficient columns
    sigma: FloatArray  # (R, n_draws)
    nu: FloatArray  # (R, n_draws)
    day_sd: FloatArray  # (R, n_draws)


def _griddy(logp: FloatArray, grid: FloatArray, rng: np.random.Generator) -> FloatArray:
    """Draw one grid value per row of ``logp`` (R, G), proportional to exp(logp)."""
    p = np.exp(logp - logp.max(axis=1, keepdims=True))
    cdf = np.cumsum(p, axis=1)
    u = rng.random((logp.shape[0], 1)) * cdf[:, -1:]
    idx = np.minimum((cdf < u).sum(axis=1), len(grid) - 1)
    return grid[idx]


def _sday_logpost(sday: FloatArray, s_d: FloatArray, t_d: FloatArray, scale: float) -> FloatArray:
    """log p(log s_day | rest) with the day effects integrated out, up to a constant.

    Per day, r_d ~ N(0, D_d^-1 + g·11'), g = s_day²: by the determinant lemma and Woodbury the
    log-likelihood is -log(1 + g·s_d)/2 + g·t_d²/(2(1 + g·s_d)), with s_d = 1'D_d1, t_d = 1'D_d r_d.
    Prior half-N(scale) on s_day, plus the log Jacobian of the log scale."""
    g = (sday**2)[:, None]
    gs = g * s_d
    loglik = np.sum(-0.5 * np.log1p(gs) + 0.5 * g * t_d**2 / (1 + gs), axis=1)
    return np.asarray(loglik - 0.5 * (sday / scale) ** 2 + np.log(sday), dtype=float)


def gibbs_t_regression(
    x: FloatArray,
    y: FloatArray,
    day: NDArray[np.intp],
    prior_mean: FloatArray,
    prior_prec: FloatArray,
    keep: Sequence[int],
    *,
    n_draws: int,
    burn_in: int,
    seed: int,
    sigma_scale: float = SIGMA_SCALE,
    day_sd_scale: float = DAY_SD_SCALE,
    nu_shape: float = NU_SHAPE,
    nu_rate: float = NU_RATE,
) -> GibbsResult:
    """Run the sampler on R data sets that share one day structure.

    x: (R, n, p) design, y: (R, n), day: (n,) day index in 0..D-1, prior_mean: (p,),
    prior_prec: (p, p). Returns draws of the coefficient columns listed in ``keep``.
    """
    rng = np.random.default_rng(seed)
    r_, n, p = x.shape
    if y.shape != (r_, n) or day.shape != (n,):
        raise ValueError("shape mismatch between x, y and day")
    n_days = int(day.max()) + 1
    z = np.zeros((n, n_days))
    z[np.arange(n), day] = 1.0
    keep_idx = np.asarray(keep, dtype=np.intp)
    xt = np.swapaxes(x, 1, 2)  # (R, p, n)
    p0m0 = prior_prec @ prior_mean  # (p,)

    # Initial values: ridge fit with unit weights, residual variance, no day effects.
    q0 = prior_prec + xt @ x
    b = np.linalg.solve(q0, (p0m0 + (xt @ y[..., None])[..., 0])[..., None])[..., 0]
    resid = y - (x @ b[..., None])[..., 0]
    sig2 = np.maximum(np.mean(resid**2, axis=1), 0.01)  # (R,)
    w = np.ones((r_, n))
    nu = np.full(r_, 10.0)
    u = np.zeros((r_, n_days))

    nu_half = _NU_GRID / 2
    nu_const = n * (nu_half * np.log(nu_half) - gammaln(nu_half))  # (G,)
    nu_logprior = (
        (nu_shape - 1) * np.log(_NU_GRID) - nu_rate * _NU_GRID + np.log(_NU_GRID)
    )  # + log-grid Jacobian
    eye_scale = 1e-10 * np.eye(p)

    total = burn_in + n_draws
    out_b = np.empty((r_, n_draws, len(keep_idx)))
    out_sigma = np.empty((r_, n_draws))
    out_nu = np.empty((r_, n_draws))
    out_sday = np.empty((r_, n_draws))
    sday = np.full(r_, 0.2)

    for it in range(total):
        d = w / sig2[:, None]  # (R, n) precisions of e_i
        # 1. s_day | b, w, sigma, with u integrated out: random-walk Metropolis on log s_day.
        r = y - (x @ b[..., None])[..., 0]
        s_d = d @ z  # (R, D)
        t_d = (d * r) @ z  # (R, D)
        prop = sday * np.exp(_SDAY_STEP * rng.standard_normal(r_))
        log_ratio = _sday_logpost(prop, s_d, t_d, day_sd_scale) - _sday_logpost(sday, s_d, t_d, day_sd_scale)
        sday = np.where(np.log(rng.random(r_)) < log_ratio, prop, sday)
        g = sday**2
        # 2. b | s_day, w, sigma, with u integrated out: Sigma^-1 = D - g·(D1)(D1)' / (1 + g·1'D1) per day.
        xd = x * d[..., None]  # (R, n, p)
        xdt = np.swapaxes(xd, 1, 2)
        c = xdt @ z  # (R, p, D)
        k = g[:, None] / (1 + g[:, None] * s_d)  # (R, D)
        ty = (d * y) @ z  # (R, D)
        prec = prior_prec + xdt @ x - (c * k[:, None, :]) @ np.swapaxes(c, 1, 2) + eye_scale
        rhs = p0m0 + (xdt @ y[..., None])[..., 0] - (c @ (k * ty)[..., None])[..., 0]
        chol = np.linalg.cholesky(prec)
        mean = np.linalg.solve(prec, rhs[..., None])[..., 0]
        noise = np.linalg.solve(np.swapaxes(chol, 1, 2), rng.standard_normal((r_, p))[..., None])[..., 0]
        b = mean + noise
        # 3. u | b, s_day, w, sigma.
        r = y - (x @ b[..., None])[..., 0]
        tr = (d * r) @ z
        u_prec = 1.0 / g[:, None] + s_d
        u = tr / u_prec + rng.standard_normal((r_, n_days)) / np.sqrt(u_prec)
        # 4. w | e, sigma, nu.
        e = r - u[:, day]
        w = rng.gamma((nu[:, None] + 1) / 2, 2.0 / (nu[:, None] + e**2 / sig2[:, None]))
        # 5. sigma² | e, w: inverse-gamma proposal, half-normal prior ratio as the acceptance.
        ss = np.sum(w * e**2, axis=1)
        prop = (ss / 2) / rng.gamma((n - 1) / 2, 1.0, size=r_)
        accept = np.log(rng.random(r_)) < -(prop - sig2) / (2 * sigma_scale**2)
        sig2 = np.where(accept, prop, sig2)
        # 6. nu | w on the grid.
        slw = np.sum(np.log(w), axis=1)
        sw = np.sum(w, axis=1)
        nu_logp = nu_const[None, :] + (nu_half[None, :] - 1) * slw[:, None] - nu_half[None, :] * sw[:, None]
        nu = _griddy(nu_logp + nu_logprior[None, :], _NU_GRID, rng)

        j = it - burn_in
        if j >= 0:
            out_b[:, j, :] = b[:, keep_idx]
            out_sigma[:, j] = np.sqrt(sig2)
            out_nu[:, j] = nu
            out_sday[:, j] = sday
    return GibbsResult(out_b, out_sigma, out_nu, out_sday)
