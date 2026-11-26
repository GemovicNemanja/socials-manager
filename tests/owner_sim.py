"""A simulated owner account for the M3 model tests: posts from a known NB2 truth, matured by
irregular runs (1-3 day gaps), so each post becomes visible at the first run 48h after it."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

from socialmgr.core.types import TrainingExample
from socialmgr.modelling.backtest import BacktestPost

T0 = datetime(2026, 3, 2, tzinfo=UTC)  # a Monday


@dataclass(frozen=True)
class Truth:
    a_in: float = math.log(0.3)
    a_oon: float = math.log(400.0)  # prior mean is log 100: 1.4 prior sds away
    beta: float = 0.8
    log_phi: float = math.log(5.0)
    lam: float = 0.25
    rho_th: float = 0.6
    reply_share: float = 0.35
    f0: float = 300.0
    f_growth: float = 5.0  # followers multiply by this over the history
    # Multiplier on the mean by post index (e.g. a level shift), 1 by default.
    level: Callable[[int, int], float] = field(default=lambda i, n: 1.0)


def mean_i48(truth: Truth, reply: bool, followers: int) -> float:
    m_in = 0.75 * truth.rho_th if reply else 1.0
    mu_in = m_in * followers**truth.beta * math.exp(truth.a_in) if followers > 0 else 0.0
    mu_oon = 0.0 if reply else math.exp(truth.a_oon)
    return mu_in + mu_oon


def simulate(truth: Truth, n_posts: int, rng: np.random.Generator) -> list[BacktestPost]:
    """About one post a day at a random hour; runs every 1-3 days; I48 drawn from the NB2 truth."""
    created = [T0 + timedelta(days=i, hours=float(rng.uniform(6, 22))) for i in range(n_posts)]
    end = created[-1] + timedelta(days=4)
    runs, t = [], T0 + timedelta(hours=23)
    while t <= end:
        runs.append(t)
        t += timedelta(days=int(rng.integers(1, 4)))
    runs.append(end)
    posts = []
    for i, c in enumerate(created):
        followers = round(truth.f0 * truth.f_growth ** (i / max(n_posts - 1, 1)))
        reply = bool(rng.uniform() < truth.reply_share)
        mu = mean_i48(truth, reply, followers) * truth.level(i, n_posts) * math.exp(truth.lam * rng.normal())
        phi = math.exp(truth.log_phi)
        y = int(rng.poisson(rng.gamma(phi, max(mu, 1e-9) / phi)))
        visible = next(r for r in runs if r >= c + timedelta(hours=48))
        posts.append(
            BacktestPost(
                example=TrainingExample(
                    post_external_id=f"p{i:04d}",
                    account_role="owner",
                    created_at=c,
                    type="reply" if reply else "original",
                    followers_at_post=followers,
                    outcome_i48=float(y),
                ),
                visible_at=visible,
                pit_seed=i + 1,
            )
        )
    return posts
