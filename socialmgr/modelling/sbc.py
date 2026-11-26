"""Simulation-based calibration of the owner NB2 model (Talts et al. 2018; MODEL.md §5 gate 6).

Each replicate draws the parameters from the prior, simulates an owner history from the model
(originals and replies, growing followers, q ~ N(0, 1)), fits it, and records the rank of each true
parameter among 99 posterior draws. With a correct sampler the ranks are uniform; a chi-square test
over 10 bins per parameter (Bonferroni over parameters at 0.01) decides.

SBC is slow, so it runs as a slow test (``tests/test_m3_owner_model.py``) and its outcome is
recorded below, keyed by the fitted model version, for the release gate to read. Changing the model,
the sampler or the prior means a new version string and a new SBC run.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from scipy import stats

from socialmgr.core.types import PriorSpec, TrainingExample
from socialmgr.modelling.models.nb2_owner import NB2OwnerModel

SBC_PARAMS = ("a_in", "a_oon", "beta", "log_phi", "lambda", "rho_th")
RANK_DRAWS = 99
BINS = 10
ALPHA = 0.01


@dataclass(frozen=True)
class SBCRecord:
    version: str
    passed: bool
    n_reps: int
    n_posts: int
    min_p: float
    recorded_on: str

    def summary(self) -> str:
        verdict = "passed" if self.passed else "failed"
        return (
            f"SBC {verdict} ({self.n_reps} replicates of {self.n_posts} posts, "
            f"min p {self.min_p:.3f}, {self.recorded_on})"
        )


# Recorded by ``tests/test_m3_owner_model.py::test_sbc_owner_model`` (slow), which fails if the
# entry for the current version is missing or disagrees with a fresh run.
SBC_RESULTS: dict[str, SBCRecord] = {
    "nb2@owner-0.2+hand_set@0.2": SBCRecord(
        version="nb2@owner-0.2+hand_set@0.2",
        passed=True,
        n_reps=300,
        n_posts=30,
        min_p=0.1132,
        recorded_on="2026-11-26",
    ),
    "nb2@owner-0.1+hand_set@0.2": SBCRecord(
        version="nb2@owner-0.1+hand_set@0.2",
        passed=True,
        n_reps=300,
        n_posts=30,
        min_p=0.0669,
        recorded_on="2026-11-26",
    ),
}


@dataclass(frozen=True)
class SBCResult:
    version: str
    ranks: dict[str, np.ndarray]
    p_values: dict[str, float]
    n_reps: int
    n_posts: int

    @property
    def min_p(self) -> float:
        return min(self.p_values.values())

    @property
    def passed(self) -> bool:
        return self.min_p > ALPHA / len(self.p_values)


def simulate_owner_history(
    truth: dict[str, float], n_posts: int, rng: np.random.Generator, reply_share: float = 0.3
) -> list[TrainingExample]:
    """Owner posts drawn from the NB2 model with q integrated by simulation."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    f0 = max(1.0, float(rng.lognormal(math.log(500), 1.0)))
    growth = float(rng.uniform(0.0, 1.5))
    out = []
    for i in range(n_posts):
        followers = round(f0 * math.exp(growth * i / max(n_posts - 1, 1)))
        reply = bool(rng.uniform() < reply_share)
        m_in = 0.75 * truth["rho_th"] if reply else 1.0
        mu_in = m_in * followers ** truth["beta"] * math.exp(truth["a_in"])
        mu_oon = 0.0 if reply else math.exp(truth["a_oon"])
        mu = max((mu_in + mu_oon) * math.exp(truth["lambda"] * rng.normal()), 1e-9)
        phi = math.exp(truth["log_phi"])
        y = int(rng.poisson(rng.gamma(phi, mu / phi)))
        out.append(
            TrainingExample(
                post_external_id=f"sim-{i}",
                account_role="owner",
                created_at=t0 + timedelta(hours=8 * i),
                type="reply" if reply else "original",
                followers_at_post=followers,
                outcome_i48=float(y),
            )
        )
    return out


def draw_truth(prior: PriorSpec, rng: np.random.Generator) -> dict[str, float]:
    p = prior.params
    return {
        "a_in": float(rng.normal(p["log_in_rate"].params["mean"], p["log_in_rate"].params["sd"])),
        "a_oon": float(rng.normal(p["log_i48_at_zero"].params["mean"], p["log_i48_at_zero"].params["sd"])),
        "beta": float(rng.normal(p["beta"].params["mean"], p["beta"].params["sd"])),
        "log_phi": float(rng.normal(p["log_phi"].params["mean"], p["log_phi"].params["sd"])),
        "lambda": float(abs(rng.normal(0.0, p["lambda"].params["sd"]))),
        "rho_th": float(rng.beta(p["rho_th"].params["a"], p["rho_th"].params["b"])),
    }


def run_sbc(
    prior: PriorSpec, n_reps: int = 300, n_posts: int = 30, seed: int = 0, model: NB2OwnerModel | None = None
) -> SBCResult:
    mdl = model if model is not None else NB2OwnerModel()
    ranks: dict[str, list[int]] = {k: [] for k in SBC_PARAMS}
    pick = np.linspace(0, 999, RANK_DRAWS).round().astype(int)
    for rep in range(n_reps):
        rng = np.random.default_rng([seed, rep, 0x5BC])
        truth = draw_truth(prior, rng)
        history = simulate_owner_history(truth, n_posts, rng)
        fitted = mdl.fit_owner(history, prior, seed=rep)
        for k in SBC_PARAMS:
            draws = fitted.draws[k][pick]
            ranks[k].append(int(np.sum(draws < truth[k])))
    p_values = {}
    for k, rs in ranks.items():
        counts = np.bincount(np.array(rs) * BINS // (RANK_DRAWS + 1), minlength=BINS)
        p_values[k] = float(stats.chisquare(counts).pvalue)
    return SBCResult(
        version=mdl.fitted_version(prior),
        ranks={k: np.array(v) for k, v in ranks.items()},
        p_values=p_values,
        n_reps=n_reps,
        n_posts=n_posts,
    )
