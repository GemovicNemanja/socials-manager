"""Batch nowcast (MODEL §2b, §4.1): precision weighting under 50 trajectories, the regression from
50, the truncation at the count already seen, and the per-age-band gate on seeded simulations."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from socialmgr.modelling import maturity
from socialmgr.modelling.nowcast import (
    MIN_TRAJECTORIES,
    PreForecast,
    Trajectory,
    age_band,
    backtest_nowcast,
    capture_estimate,
    fit_regression,
    nowcast,
    nowcast_gate,
)

T0 = datetime(2026, 4, 1, tzinfo=UTC)
PRE_SD = 0.8  # predictive log-sd of the pre-publication forecast in the simulation


def _pre(log_mean: float, rng: np.random.Generator) -> PreForecast:
    return PreForecast.from_draws(np.round(np.exp(rng.normal(log_mean, PRE_SD, 1000))))


def _trajectories(
    n: int, rng: np.random.Generator, curve: maturity.Curve = maturity.PRIOR
) -> list[Trajectory]:
    """Calibrated pre-publication forecasts; early counts thinned from I48 along ``curve``."""
    out = []
    for i in range(n):
        log_mean = float(rng.normal(math.log(300), 0.5))
        i48 = round(math.exp(rng.normal(log_mean, PRE_SD)))
        age = float(rng.uniform(0.5, 47.5))
        g = curve.g(age)
        p = rng.beta(g * 100, (1 - g) * 100)  # BetaBinomial thinning, kappa = 100
        count = int(rng.binomial(i48, p))
        created = T0 + timedelta(hours=8 * i)
        out.append(
            Trajectory(
                post_id=f"t{i}",
                age_h=age,
                count=count,
                outcome_i48=float(i48),
                followers=500 + i,
                pre=_pre(log_mean, rng),
                created_at=created,
                visible_at=created + timedelta(hours=72),
            )
        )
    return out


def test_age_bands() -> None:
    assert [age_band(a) for a in (0.1, 5.99, 6.0, 23.9, 24.0, 47.9)] == [
        "0-6h",
        "0-6h",
        "6-24h",
        "6-24h",
        "24-48h",
        "24-48h",
    ]
    with pytest.raises(ValueError):
        age_band(48.0)


def test_capture_estimate_gets_sharper_with_age_and_count() -> None:
    m1, v1 = capture_estimate(50, 1.0, maturity.PRIOR)
    m24, v24 = capture_estimate(50, 24.0, maturity.PRIOR)
    assert m1 > m24 and v1 > v24  # 50 at 1h implies more than 50 at 24h, less surely
    _, v_small = capture_estimate(5, 24.0, maturity.PRIOR)
    assert v_small > v24


def test_precision_weighting_sits_between_and_respects_the_count() -> None:
    rng = np.random.default_rng(0)
    pre = _pre(math.log(200), rng)
    cap_mean, _ = capture_estimate(900, 20.0, maturity.PRIOR)
    nc = nowcast(900, 20.0, pre, 500, maturity.PRIOR)
    assert nc.method == "precision_weighting" and nc.provisional and nc.age_band == "6-24h"
    assert pre.log_mean < nc.log_loc < cap_mean
    assert nc.quantiles["q05"] >= 900  # impressions already seen are a lower bound
    assert list(nc.quantiles.values()) == sorted(nc.quantiles.values())
    assert nc.p_above_pre_median == 1.0  # the count already passed the pre-publication median
    low = nowcast(3, 30.0, pre, 500, maturity.PRIOR)
    assert 0.0 <= low.p_above_pre_median < 0.2
    assert np.all(nc.samples() >= 900) and nc.samples().shape == (1000,)


def test_regression_from_fifty_trajectories() -> None:
    rng = np.random.default_rng(1)
    assert fit_regression(_trajectories(MIN_TRAJECTORIES - 1, rng)) is None
    reg = fit_regression(_trajectories(200, rng))
    assert reg is not None and reg.n == 200
    pre = _pre(math.log(300), rng)
    nc = nowcast(400, 20.0, pre, 600, maturity.PRIOR, reg)
    assert nc.method == "regression" and nc.dof == 5.0 and nc.n_trajectories == 200
    # b_t (weight on the capture) grows with age: the capture is more informative later.
    b0, b1 = reg.coef[2], reg.coef[3]
    assert b0 > 0.3 and b1 > 0


def _gate_passes(n: int, seed: int, curve_used: maturity.Curve = maturity.PRIOR) -> tuple[bool, set[str]]:
    rows = backtest_nowcast(_trajectories(n, np.random.default_rng(seed)), curve_used)
    gate = nowcast_gate(rows)
    return gate.passed, {r.method for r in rows}


def test_nowcast_gate_passes_with_precision_weighting_and_with_the_regression() -> None:
    """Replicated: with fewer than 50 visible trajectories only precision weighting runs; on longer
    histories the regression takes over. Both beat the pre-publication forecast in every band."""
    for seed in range(3):
        passed, methods = _gate_passes(45, seed)
        assert passed and methods == {"precision_weighting"}
    for seed in range(3):
        passed, methods = _gate_passes(160, 100 + seed)
        assert passed and methods == {"precision_weighting", "regression"}


def test_nowcast_gate_fails_with_a_wrong_maturity_curve() -> None:
    """A curve that thinks posts gather impressions slowly over a day (S-shaped, half at ~19h) turns
    early captures into overestimates: skill goes negative in the early bands."""
    wrong = maturity.Curve("wrong", 24.0, 1.5)
    fails = [not _gate_passes(45, seed, wrong)[0] for seed in range(3)]
    assert all(fails)
    assert not nowcast_gate([]).passed  # no evidence in a band fails it
