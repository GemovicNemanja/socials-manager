"""Trailing-median baseline (MODEL.md §2a fallback 2): trailing median * (F / F_ref)^0.75.

It is also the benchmark every later model must beat in the release gates (MODEL §5).
With fewer than ``MIN_HISTORY`` matured originals it falls back to the prior.
"""

import hashlib
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from socialmgr.core.types import ForecastSubject, PredictiveDraws, PriorSpec, TrainingExample

N_DRAWS = 1000
WINDOW = 21
MIN_HISTORY = 5
ELASTICITY = 0.75
# Placeholder reach multipliers by post type relative to an original (MODEL C1: replies get no
# out-of-network reach). Replaced by the NB2 type terms in M1.
TYPE_MULTIPLIER = {"original": 1.0, "quote": 1.0, "reply": 0.15, "self_reply": 0.1, "repost": 0.1}


@dataclass(frozen=True)
class FittedTrailingMedian:
    version: str
    log_level: float  # log I48 for an original at the reference follower count
    f_ref: float
    level_sd: float  # uncertainty in the level
    noise_sd: float  # post-to-post spread on the log scale
    seed: int

    def predict(self, subject: ForecastSubject) -> PredictiveDraws:
        # Seeded from the subject id (not Python's salted hash) so a forecast recomputes bit-for-bit.
        subject_seed = int.from_bytes(hashlib.sha256(subject.subject_id.encode()).digest()[:4], "big")
        rng = np.random.default_rng([self.seed, subject_seed])
        log_mu = (
            self.log_level
            + ELASTICITY * (math.log1p(subject.followers_at_post) - math.log1p(self.f_ref))
            + math.log(TYPE_MULTIPLIER.get(subject.type, 1.0))
        )
        sd = math.hypot(self.level_sd, self.noise_sd)
        samples = np.exp(rng.normal(log_mu, sd, N_DRAWS))
        return PredictiveDraws(metric="impressions", horizon_h=48.0, samples=samples)


class TrailingMedianModel:
    family = "trailing_median"
    version = "0.1"

    def fit(self, examples: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedTrailingMedian:
        originals = sorted(
            (e for e in examples if e.account_role == "owner" and e.type in ("original", "quote")),
            key=lambda e: e.created_at,
        )[-WINDOW:]
        tag = f"{self.family}@{self.version}+{prior.source}@{prior.version}"
        if len(originals) < MIN_HISTORY:
            level = prior.params["log_i48_at_zero"].params
            return FittedTrailingMedian(
                version=tag,
                log_level=level["mean"],
                f_ref=0.0,
                level_sd=level["sd"],
                noise_sd=prior.params["log_sd"].params["sd"],
                seed=seed,
            )
        logs = [math.log1p(e.outcome_i48) for e in originals]
        f_ref = statistics.median(e.followers_at_post for e in originals)
        noise = max(statistics.pstdev(logs), 0.5)
        return FittedTrailingMedian(
            version=tag,
            log_level=statistics.median(logs),
            f_ref=float(f_ref),
            level_sd=noise / math.sqrt(len(logs)),
            noise_sd=noise,
            seed=seed,
        )
