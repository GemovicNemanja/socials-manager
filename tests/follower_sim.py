"""Simulated follower histories with known parameters (MODEL.md §2c, batch mode)."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from socialmgr.modelling.followers import RunInterval

START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Truth:
    pi: float  # follows per profile click (L2) or per 1k impressions (L3)
    organic: float  # organic follows per day
    phi_gain: float
    loss_rate: float  # per follower per day
    phi_loss: float
    level: str = "L2"


def nb(rng: np.random.Generator, mu: float, phi: float) -> int:
    return int(rng.poisson(rng.gamma(phi, max(mu, 1e-12) / phi)))


def simulate_history(
    rng: np.random.Generator,
    truth: Truth,
    days: float = 120.0,
    followers0: int = 800,
    clicks_per_day: float = 20.0,
    clicks_spread: float = 0.7,
    pc_per_1k: float = 5.0,
    missing_rate: float = 0.1,
    start: datetime = START,
) -> list[RunInterval]:
    """Runs every 1-3 days (±6h jitter); data generated exactly as the model states it per interval."""
    out: list[RunInterval] = []
    t = start
    f = followers0
    end = start + timedelta(days=days)
    while True:
        gap = float(rng.choice([1.0, 2.0, 3.0])) + float(rng.uniform(-0.25, 0.25))
        t_next = t + timedelta(days=gap)
        if t_next > end:
            break
        clicks = nb(
            rng, clicks_per_day * gap * math.exp(clicks_spread * rng.normal() - clicks_spread**2 / 2), 3.0
        )
        impressions = float(nb(rng, clicks * 1000.0 / pc_per_1k, 20.0)) if clicks > 0 else 0.0
        x = clicks if truth.level == "L2" else impressions / 1000.0
        gains = nb(rng, truth.organic * gap + truth.pi * x, truth.phi_gain)
        losses = nb(rng, truth.loss_rate * f * gap, truth.phi_loss)
        f_next = max(f + gains - losses, 0)
        missing = bool(rng.uniform() < missing_rate)
        out.append(
            RunInterval(
                start=t,
                end=t_next,
                followers_start=f,
                followers_end=f_next,
                gains=None if missing else gains,
                gains_complete=not missing,
                impressions=impressions,
                profile_clicks=float(clicks) if truth.level == "L2" else None,
            )
        )
        t, f = t_next, f_next
    return out
