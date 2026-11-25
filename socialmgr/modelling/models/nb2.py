"""Prior-only NB2 reach model (MODEL.md §2a, single account, no peers).

    I ~ NB2(mu_in + mu_oon, phi)
    mu_in  = m_in(type)  * F^beta * exp(a_in + lambda q)
    mu_oon = m_oon(type) * exp(a_oon + theta_url * has_url + lambda q),   q ~ N(0, 1)
    m_in:  original/quote 1, reply/self-reply 0.75 rho_th      m_oon: original/quote 1, replies 0

M1 draws every parameter from its prior (``prior_only_uncalibrated``, A22). M3 adds owner-level
intercepts and dispersion once 20 owner posts have matured; ``fit`` already receives them.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from socialmgr.core.types import Distribution, ForecastSubject, PredictiveDraws, PriorSpec, TrainingExample

N_DRAWS = 1000
REPLY_TYPES = frozenset({"reply", "self_reply"})


def _draw(rng: np.random.Generator, dist: Distribution, n: int) -> np.ndarray:
    p = dist.params
    match dist.family:
        case "normal":
            return rng.normal(p["mean"], p["sd"], n)
        case "half_normal":
            return np.abs(rng.normal(0.0, p["sd"], n))
        case "beta":
            return rng.beta(p["a"], p["b"], n)
        case "lognormal":
            return np.exp(rng.normal(p["mean"], p["sd"], n))
        case "gamma":
            return rng.gamma(p["shape"], p["scale"], n)
    raise ValueError(f"unsupported prior family {dist.family}")


@dataclass(frozen=True)
class FittedNB2:
    version: str
    prior: PriorSpec
    seed: int
    owner_posts: int = 0
    meta: dict[str, str] = field(default_factory=dict)

    def predict(self, subject: ForecastSubject) -> PredictiveDraws:
        subject_seed = int.from_bytes(hashlib.sha256(subject.subject_id.encode()).digest()[:4], "big")
        rng = np.random.default_rng([self.seed, subject_seed])
        p = self.prior.params
        n = N_DRAWS
        beta = _draw(rng, p["beta"], n)
        a_oon = _draw(rng, p["log_i48_at_zero"], n)
        a_in = _draw(rng, p["log_in_rate"], n)
        theta_url = _draw(rng, p["theta_url"], n)
        log_phi = _draw(rng, p["log_phi"], n)
        lam = _draw(rng, p["lambda"], n)
        rho = _draw(rng, p["rho_th"], n)
        q = rng.normal(0.0, 1.0, n)

        is_reply = subject.type in REPLY_TYPES
        m_in = 0.75 * rho if is_reply else np.ones(n)
        m_oon = 0.0 if is_reply else 1.0
        followers = float(max(subject.followers_at_post, 0))
        quality = np.exp(lam * q)
        mu_in = m_in * np.power(followers, beta) * np.exp(a_in) if followers > 0 else np.zeros(n)
        mu_oon = m_oon * np.exp(a_oon + theta_url * float(subject.has_url))
        mu = np.maximum((mu_in + mu_oon) * quality, 1e-9)
        phi = np.exp(log_phi)
        rate = rng.gamma(phi, mu / phi)  # NB2 as a gamma-Poisson mixture
        samples = rng.poisson(rate).astype(np.float64)
        return PredictiveDraws(
            metric="impressions",
            horizon_h=48.0,
            samples=samples,
            meta={"uses_owner_data": "false", "owner_posts_seen": str(self.owner_posts)},
        )


class NB2PriorModel:
    family = "nb2"
    version = "prior-0.1"

    def fit(self, examples: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedNB2:
        owner_n = sum(1 for e in examples if e.account_role == "owner")
        return FittedNB2(
            version=f"{self.family}@{self.version}+{prior.source}@{prior.version}",
            prior=prior,
            seed=seed,
            owner_posts=owner_n,
        )
