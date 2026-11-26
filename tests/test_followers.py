"""Follower model, forecast, baselines and scoring (MODEL.md §2c, §4.1; plan §9 M3 acceptance).

Acceptance criteria owned here:
- "The follower DGLM recovers known parameters": simulated interval data with known π, organic
  level, φ_G and loss rate under irregular 1-3 day gaps; coverage of 90% intervals and bias.
- "Goal probabilities match a 100,000-path reference within ±1 point": the 4,000-path estimate
  against a 100,000-path reference, for goals spanning p ≈ 0.05-0.95, across many seeds.
The replicated versions are marked slow; the default run keeps small, fast versions.
"""

import json
import math
from collections.abc import Callable
from datetime import datetime, timedelta

import numpy as np
import pytest

from socialmgr.modelling.followers import (
    ArrayImpressionSampler,
    FollowerFit,
    FollowerForecast,
    FollowerGoal,
    RunInterval,
    RunObservation,
    Scenario,
    fit_follower_model,
    forecast_followers,
    forecast_from_manifest,
    intervals_from_runs,
    local_linear_trend,
    persistence_drift,
    score_follower_forecast,
    sum_increments,
)
from socialmgr.modelling.followers.model import GainPredictive
from tests.follower_sim import START, Truth, nb, simulate_history

TRUTH_L2 = Truth(pi=0.1, organic=2.0, phi_gain=5.0, loss_rate=0.002, phi_loss=5.0, level="L2")
TRUTH_L3 = Truth(pi=0.5, organic=2.0, phi_gain=5.0, loss_rate=0.002, phi_loss=5.0, level="L3")


def _sampler(fit: FollowerFit, seed: int = 7) -> ArrayImpressionSampler:
    rng = np.random.default_rng(seed)
    draws = {
        "original": tuple(rng.poisson(rng.gamma(1.5, 1200 / 1.5, 1000)).astype(float)),
        "reply": tuple(rng.poisson(rng.gamma(1.0, 150.0, 1000)).astype(float)),
    }
    return ArrayImpressionSampler(draws, followers_ref=fit.followers_last or 800)


def _fit(days: float, seed: int = 1, truth: Truth = TRUTH_L2) -> FollowerFit:
    return fit_follower_model(simulate_history(np.random.default_rng(seed), truth, days=days))


# --- inputs ------------------------------------------------------------------------------------------


def test_run_interval_validates_and_round_trips() -> None:
    iv = RunInterval(START, START + timedelta(days=2, hours=6), 100, 104, gains=6, impressions=1500.0)
    assert iv.days == pytest.approx(2.25)
    assert iv.net_change == 4 and iv.gains_observed
    assert RunInterval.from_dict(json.loads(json.dumps(iv.to_dict()))) == iv
    with pytest.raises(ValueError):
        RunInterval(datetime(2026, 6, 1), datetime(2026, 6, 2), 1, 1)
    with pytest.raises(ValueError):
        RunInterval(START, START, 1, 1)
    assert not RunInterval(
        START, START + timedelta(days=1), 1, 1, gains=3, gains_complete=False
    ).gains_observed


def test_intervals_from_runs_pairs_consecutive_runs() -> None:
    runs = [
        RunObservation(
            START + timedelta(days=3), 110, new_followers=7, impressions=900.0, profile_clicks=4.0
        ),
        RunObservation(START, 100, new_followers=None, new_followers_complete=False),
        RunObservation(START + timedelta(days=1), 103, new_followers=4, new_followers_complete=False),
    ]
    ivs = intervals_from_runs(runs)
    assert [(iv.followers_start, iv.followers_end) for iv in ivs] == [(100, 103), (103, 110)]
    assert not ivs[0].gains_observed and ivs[1].gains == 7 and ivs[1].profile_clicks == 4.0


def test_sum_increments_counts_new_posts_from_zero() -> None:
    assert sum_increments({"a": 100, "b": 50}, {"a": 160, "b": 50, "c": 30}) == 90.0
    assert sum_increments({"a": 100}, {"a": 90}) == 0.0  # never negative
    assert sum_increments({"a": 100}, {"a": None}) is None


# --- fit ---------------------------------------------------------------------------------------------------


def test_level_follows_available_covariates() -> None:
    ivs = simulate_history(np.random.default_rng(2), TRUTH_L2, days=60)
    assert fit_follower_model(ivs).level == "L2"
    no_pc = [RunInterval(**{**iv.__dict__, "profile_clicks": None}) for iv in ivs]
    assert fit_follower_model(no_pc).level == "L3"
    bare = [RunInterval(**{**iv.__dict__, "profile_clicks": None, "impressions": None}) for iv in ivs]
    fit = fit_follower_model(bare)
    assert fit.level == "L0" and fit.pi == 0.0


def test_first_four_weeks_are_prior_only() -> None:
    short = _fit(20)
    assert short.label == "prior_only"
    assert short.theta[short.theta_names.index("t_pi")] == pytest.approx(math.log(0.1 / 0.9))
    assert short.phi_gain == pytest.approx(4.0)
    long = _fit(60)
    assert long.label == "fitted" and long.span_days >= 28


def test_fit_is_deterministic_and_json_round_trips() -> None:
    ivs = simulate_history(np.random.default_rng(3), TRUTH_L2, days=90)
    a, b = fit_follower_model(ivs), fit_follower_model(ivs)
    assert a.to_dict() == b.to_dict()
    back = FollowerFit.from_dict(json.loads(json.dumps(a.to_dict())))
    assert back.to_dict() == a.to_dict()
    assert back.pi == a.pi and back.one_step_predictive(2.0, 40.0) == a.one_step_predictive(2.0, 40.0)


def test_missing_gains_skip_the_gain_likelihood_but_keep_net_change() -> None:
    ivs = simulate_history(np.random.default_rng(4), TRUTH_L2, days=90, missing_rate=0.0)
    blanked = [
        RunInterval(**{**iv.__dict__, "gains": None, "gains_complete": False}) if k % 3 == 0 else iv
        for k, iv in enumerate(ivs)
    ]
    fit = fit_follower_model(blanked)
    assert fit.n_gain_obs == len(ivs) - len(range(0, len(ivs), 3))
    assert fit.n_loss_obs == fit.n_gain_obs
    gaps = [d for d in fit.diagnostics if d.observed is None]
    assert len(gaps) == len(range(0, len(ivs), 3)) and all(d.z is None for d in gaps)
    # the level still tracks: with net changes only, the organic level stays near the truth
    assert 1.0 < fit.organic_per_day < 4.0


def test_negative_implied_losses_are_clipped_and_counted() -> None:
    t = START
    ivs = []
    f = 500
    for _ in range(20):
        ivs.append(RunInterval(t, t + timedelta(days=2), f, f + 10, gains=6, profile_clicks=30.0))
        t += timedelta(days=2)
        f += 10
    fit = fit_follower_model(ivs)
    assert fit.n_negative_losses == 20


def test_zero_heavy_gains_switch_on_the_zero_inflated_component() -> None:
    rng = np.random.default_rng(11)
    truth = Truth(pi=0.05, organic=1.5, phi_gain=8.0, loss_rate=0.001, phi_loss=5.0)
    ivs = simulate_history(rng, truth, days=120, missing_rate=0.0)
    zeroed = [
        RunInterval(**{**iv.__dict__, "gains": 0, "followers_end": iv.followers_start})
        if rng.uniform() < 0.6
        else iv
        for iv in ivs
    ]
    fit = fit_follower_model(zeroed)
    assert fit.zero_inflated and 0.3 < fit.omega < 0.9
    assert not _fit(120, seed=5).zero_inflated


def _recovery(truth: Truth, replicates: int, seed0: int) -> dict[str, float]:
    t_pi = math.log(truth.pi / (1 - truth.pi)) if truth.level == "L2" else math.log(truth.pi)
    hits = {"pi": 0, "phi_gain": 0, "phi_loss": 0, "organic": 0, "loss_rate": 0}
    err_pi, err_org, err_rho = [], [], []
    for r in range(replicates):
        fit = fit_follower_model(simulate_history(np.random.default_rng(seed0 + r), truth, days=120))
        lo, hi = fit.interval("t_pi")
        hits["pi"] += lo <= t_pi <= hi
        lo, hi = fit.interval("log_phi_gain")
        hits["phi_gain"] += lo <= math.log(truth.phi_gain) <= hi
        lo, hi = fit.interval("log_phi_loss")
        hits["phi_loss"] += lo <= math.log(truth.phi_loss) <= hi
        hits["organic"] += abs(fit.eta_mean - math.log(truth.organic)) <= 1.645 * math.sqrt(fit.eta_var)
        hits["loss_rate"] += abs(fit.log_rho_mean - math.log(truth.loss_rate)) <= 1.645 * math.sqrt(
            fit.log_rho_var
        )
        err_pi.append(math.log(fit.pi / truth.pi))
        err_org.append(fit.eta_mean - math.log(truth.organic))
        err_rho.append(fit.log_rho_mean - math.log(truth.loss_rate))
    out = {f"cover_{k}": v / replicates for k, v in hits.items()}
    out |= {"bias_log_pi": float(np.mean(err_pi)), "bias_log_organic": float(np.mean(err_org))}
    out |= {"bias_log_loss_rate": float(np.mean(err_rho))}
    return out


def test_recovers_known_parameters_small() -> None:
    res = _recovery(TRUTH_L2, 30, seed0=100)
    assert res["cover_pi"] >= 0.75 and res["cover_phi_gain"] >= 0.75
    assert abs(res["bias_log_pi"]) < 0.1 and abs(res["bias_log_organic"]) < 0.15


@pytest.mark.slow
@pytest.mark.parametrize("truth", [TRUTH_L2, TRUTH_L3], ids=["L2", "L3"])
def test_follower_dglm_recovers_known_parameters(truth: Truth) -> None:
    """M3: 1,000 replicates of 120 days, runs every 1-3 days (±6h), 10% of gain lists missing.

    Each 90% interval (Laplace for π, φ_G, φ_L; filtered for the final organic level and loss rate)
    covers the truth at 0.86-0.97 (±3.5 binomial sd at 1,000 replicates, plus a little for the
    Laplace approximation), and the mean log-scale bias is under 0.05 (5%).
    """
    res = _recovery(truth, 1000, seed0=10_000)
    print(truth.level, res)
    for key in ("pi", "phi_gain", "phi_loss", "organic", "loss_rate"):
        assert 0.86 <= res[f"cover_{key}"] <= 0.97, (key, res)
    for key in ("bias_log_pi", "bias_log_organic", "bias_log_loss_rate"):
        assert abs(res[key]) < 0.05, (key, res)


# --- one-step predictive and the drift detector's z -------------------------------------------------


def test_randomized_pit_is_seeded_and_z_is_calibrated() -> None:
    pred = GainPredictive(mean=6.0, phi=3.0)
    assert pred.randomized_pit(5, "followers:x") == pred.randomized_pit(5, "followers:x")
    assert pred.randomized_pit(5, "followers:x") != pred.randomized_pit(5, "followers:y")
    assert 0.0 <= pred.randomized_pit(0, "a") <= pred.cdf(0)
    zs = []
    for r in range(12):
        fit = _fit(150, seed=300 + r)
        zs += [d.z for d in fit.diagnostics[5:] if d.z is not None]
    z = np.array(zs)
    assert abs(z.mean()) < 0.15 and 0.85 < z.std() < 1.15


def test_one_step_predictive_scales_with_covariate() -> None:
    fit = _fit(90)
    quiet, busy = fit.one_step_predictive(2.0, 0.0), fit.one_step_predictive(2.0, 100.0)
    assert busy.mean == pytest.approx(quiet.mean + fit.pi * 100.0, rel=1e-6)
    assert quiet.mean == pytest.approx(2.0 * fit.organic_per_day, rel=0.2)
    assert 0 < busy.cdf(busy.mean) < 1 and math.isfinite(busy.z(int(busy.mean), "s"))


# --- baselines -------------------------------------------------------------------------------------------


def test_baselines_follow_the_trend() -> None:
    times = [START + timedelta(days=2 * k) for k in range(30)]
    counts = [500 + 6 * k for k in range(30)]  # 3 a day
    pd_ = persistence_drift(times, counts, horizons=(7, 30))
    assert pd_.mean[30] == pytest.approx(counts[-1] + 90, abs=1)
    llt = local_linear_trend(times, counts, horizons=(7, 30))
    assert llt.mean[30] == pytest.approx(counts[-1] + 90, abs=5)
    assert llt.draws[7].shape == (4000,) and llt.sd[30] > llt.sd[7]


# --- forecast -------------------------------------------------------------------------------------------


def test_forecast_is_reproducible_from_its_manifest() -> None:
    fit = _fit(90)
    fc = forecast_followers(
        fit, {"original": 3.0, "reply": 1.5}, _sampler(fit), goal=FollowerGoal(950, 30), seed=42
    )
    manifest = json.loads(json.dumps(fc.manifest))
    again = forecast_from_manifest(manifest)
    assert again.manifest_hash == fc.manifest_hash
    for d in fc.checkpoints:
        assert np.array_equal(again.draws[d], fc.draws[d])
    assert again.p_goal == fc.p_goal
    assert [s.to_dict() for s in again.scenarios] == [s.to_dict() for s in fc.scenarios]
    other = forecast_followers(
        fit, {"original": 3.0, "reply": 1.5}, _sampler(fit), goal=FollowerGoal(950, 30), seed=43
    )
    assert not np.array_equal(other.draws[30], fc.draws[30])


def test_forecast_outputs_and_round_trip() -> None:
    fit = _fit(90)
    fc = forecast_followers(fit, {"original": 3.0}, _sampler(fit), goal=FollowerGoal(1000, 45), seed=1)
    assert fc.checkpoints == (7, 30, 45, 90)
    for d in fc.checkpoints:
        qs = list(fc.quantiles[d].values())
        assert qs == sorted(qs) and fc.draws[d].shape == (4000,)
    assert fc.quantiles[90]["q50"] > fc.quantiles[7]["q50"] > fc.followers_now - 20
    assert fc.p_goal is not None and 0.0 <= fc.p_goal <= 1.0
    assert fc.prob_ge(45, 1000) == fc.p_goal
    assert fc.prob_lt(45, 1000.0) == pytest.approx(1.0 - fc.p_goal)
    blob = json.loads(json.dumps(fc.to_dict()))
    back = FollowerForecast.from_dict(blob)
    assert back.quantiles == fc.quantiles and back.p_goal == fc.p_goal
    assert np.array_equal(back.draws[45], fc.draws[45])
    assert back.prob_ge(45, 1000) == pytest.approx(float(np.mean(fc.draws[45] >= 1000)))


def test_what_if_scenarios_order_by_posting_volume() -> None:
    fit = _fit(90)
    fc = forecast_followers(fit, {"original": 3.0}, _sampler(fit), goal=FollowerGoal(1050, 90), seed=3)
    by_name = {s.name: s for s in fc.scenarios}
    two, three, four = (by_name[f"{k} originals a day"] for k in (2, 3, 4))
    assert two.quantiles[90]["q50"] < three.quantiles[90]["q50"] < four.quantiles[90]["q50"]
    assert two.p_goal is not None and four.p_goal is not None and two.p_goal < four.p_goal
    assert three.quantiles[90] == fc.quantiles[90]  # same seed, same schedule: same paths
    mix = by_name["2 originals and 3 replies a day"]
    assert mix.skipped is None and mix.quantiles[90]["q50"] >= two.quantiles[90]["q50"]
    only_originals = ArrayImpressionSampler({"original": (500.0, 900.0)}, followers_ref=800)
    fc2 = forecast_followers(
        fit, {"original": 2.0}, only_originals, scenarios=[Scenario("r", {"reply": 1.0})]
    )
    assert fc2.scenarios[0].skipped is not None


def test_forecast_rejects_bad_inputs() -> None:
    fit = _fit(90)
    sampler = _sampler(fit)
    with pytest.raises(ValueError):
        forecast_followers(fit, {"quote": 1.0}, sampler)
    with pytest.raises(ValueError):
        forecast_followers(fit, {"original": 1.0}, sampler, horizon_days=30, goal=FollowerGoal(900, 60))
    with pytest.raises(ValueError):
        FollowerGoal(900, 120)

    class External:
        sampler_id = "reach-model@x"
        uses_followers = False

        def post_types(self) -> frozenset[str]:
            return frozenset({"original"})

        def quantile(self, post_type: str, u: np.ndarray, followers: np.ndarray) -> np.ndarray:
            return np.asarray(1000.0 * u)

        def to_dict(self) -> None:
            return None

    fc = forecast_followers(fit, {"original": 2.0}, External(), scenarios=None, seed=5)
    with pytest.raises(ValueError):
        forecast_from_manifest(fc.manifest)
    assert np.array_equal(forecast_from_manifest(fc.manifest, External()).draws[30], fc.draws[30])


def test_forecast_runtime_is_seconds() -> None:
    import time

    fit = _fit(90)
    t0 = time.perf_counter()
    forecast_followers(
        fit, {"original": 3.0, "reply": 1.5}, _sampler(fit), goal=FollowerGoal(1000, 30), scenarios=None
    )
    assert time.perf_counter() - t0 < 5.0


# --- goal probabilities: acceptance ---------------------------------------------------------------------

P_TARGETS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95)


def _reference(
    fit: FollowerFit, horizon: int, paths: int, chunk: int = 5000
) -> tuple[list[int], list[float], list[float]]:
    """Goals at the targets' quantiles of a ``paths``-path run; reference P by the naive indicator
    and by the variance-reduced estimator, each averaged over independent chunks."""
    sampler = _sampler(fit)
    draws, chunks = [], []
    for s in range(paths // chunk):
        fc = forecast_followers(
            fit,
            {"original": 3.0, "reply": 1.5},
            sampler,
            horizon_days=horizon,
            n_paths=chunk,
            seed=1_000_000 + s,
            scenarios=None,
            checkpoints=(horizon,),
            probability_days=(horizon,),
        )
        draws.append(fc.draws[horizon])
        chunks.append(fc)
    allf = np.concatenate(draws)
    goals = [int(np.quantile(allf, 1.0 - p)) for p in P_TARGETS]
    naive = [float(np.mean(allf >= g)) for g in goals]
    vr = [float(np.mean([fc.prob_ge(horizon, g) for fc in chunks])) for g in goals]
    return goals, naive, vr


def _errors(fit: FollowerFit, horizon: int, goals: list[int], ref: list[float], seeds: range) -> np.ndarray:
    sampler = _sampler(fit)
    out = []
    for s in seeds:
        fc = forecast_followers(
            fit,
            {"original": 3.0, "reply": 1.5},
            sampler,
            horizon_days=horizon,
            goal=FollowerGoal(goals[len(goals) // 2], horizon),
            seed=s,
            scenarios=None,
            checkpoints=(horizon,),
        )
        out.append([fc.prob_ge(horizon, g) - r for g, r in zip(goals, ref, strict=True)])
    return np.array(out)


def test_goal_probability_matches_reference_small() -> None:
    fit = _fit(120)
    goals, naive, vr = _reference(fit, 30, paths=40_000)
    assert max(abs(a - b) for a, b in zip(naive, vr, strict=True)) < 0.01  # the estimator is unbiased
    err = _errors(fit, 30, goals, vr, range(3))
    assert np.abs(err).max() <= 0.01


FITS: dict[str, Callable[[], FollowerFit]] = {"fitted": lambda: _fit(120), "prior_only": lambda: _fit(20)}


@pytest.mark.slow
@pytest.mark.parametrize("regime", ["fitted", "prior_only"])
@pytest.mark.parametrize("horizon", [30, 90])
def test_goal_probability_matches_100k_reference(regime: str, horizon: int) -> None:
    """M3: 4,000-path P(F_T ≥ goal) within ±1 point of a 100,000-path reference, for goals at
    p ≈ 0.05-0.95, over 100 seeds. The reference is the variance-reduced estimator averaged over
    20 independent 5,000-path runs (SE ≈ 0.03 points); the naive 100,000-path indicator must agree
    with it within its own sampling error (SE ≤ 0.16 points; checked at 1 point)."""
    fit = FITS[regime]()
    goals, naive, vr = _reference(fit, horizon, paths=100_000)
    assert max(abs(a - b) for a, b in zip(naive, vr, strict=True)) < 0.01
    err = _errors(fit, horizon, goals, vr, range(100))
    within = float(np.mean(np.abs(err) <= 0.01))
    print(
        regime,
        horizon,
        "pass rate",
        within,
        "max |err|",
        float(np.abs(err).max()),
        "sd",
        err.std(axis=0).round(4),
    )
    assert within == 1.0


# --- calibration of the forecast against simulated truth -----------------------------------------------


@pytest.mark.slow
def test_forecast_covers_simulated_futures() -> None:
    """Fit on 120 simulated days, forecast 30 days with a sampler that matches the simulator's click
    process, then simulate the true continuation. Over 300 replicates the 80% interval of F_30 covers
    the truth at least 75% of the time and the mid-PITs aren't lopsided."""
    pits, inside = [], []
    pc_per_1k = 5.0
    for r in range(300):
        rng = np.random.default_rng(50_000 + r)
        ivs = simulate_history(rng, TRUTH_L2, days=150, missing_rate=0.0, pc_per_1k=pc_per_1k)
        cut = max(k for k, iv in enumerate(ivs) if iv.end <= START + timedelta(days=120))
        hist, future = ivs[: cut + 1], ivs[cut + 1 :]
        fit = fit_follower_model(hist)
        target_end = hist[-1].end + timedelta(days=30)
        horizon_iv = min(future, key=lambda iv: abs((iv.end - target_end).total_seconds()))
        horizon = round((horizon_iv.end - hist[-1].end).total_seconds() / 86_400.0)
        if not 1 <= horizon <= 90:
            continue
        # one post a day whose impressions give the simulator's mean of 20 clicks a day
        srng = np.random.default_rng(r)
        draws = 20.0 * 1000.0 / pc_per_1k * np.exp(0.7 * srng.standard_normal(2000) - 0.245)
        sampler = ArrayImpressionSampler({"original": tuple(draws)}, followers_ref=hist[-1].followers_end)
        fc = forecast_followers(
            fit,
            {"original": 1.0},
            sampler,
            horizon_days=horizon,
            n_paths=2000,
            checkpoints=(horizon,),
            scenarios=None,
            seed=r,
        )
        score = score_follower_forecast(fc.draws[horizon], horizon_iv.followers_end, horizon_days=horizon)
        pits.append(score.pit_mid)
        inside.append(score.in_80)
    pit = np.array(pits)
    print(
        "coverage80",
        np.mean(inside),
        "pit mean",
        pit.mean(),
        "below .2",
        np.mean(pit <= 0.2),
        "above .8",
        np.mean(pit >= 0.8),
    )
    assert np.mean(inside) >= 0.75
    assert 0.4 < pit.mean() < 0.6


# --- scoring --------------------------------------------------------------------------------------------


def test_score_follower_forecast_labels_outcomes() -> None:
    draws = np.random.default_rng(0).poisson(1000, 4000)
    mid = score_follower_forecast(draws, 1000, horizon_days=30, followers_at_forecast=900)
    assert mid.outcome == "as_expected" and mid.in_50 and mid.in_80
    assert mid.expected_growth == pytest.approx(100, abs=2) and mid.observed_growth == 100
    hi = score_follower_forecast(draws, 1100, horizon_days=30)
    lo = score_follower_forecast(draws, 900, horizon_days=30)
    assert hi.outcome == "above" and lo.outcome == "below"
    assert hi.crps_log > mid.crps_log >= 0.0
    assert score_follower_forecast(draws, 1000, horizon_days=30).to_dict()["outcome"] == "as_expected"


def test_frozen_forecast_scores_when_horizon_passes() -> None:
    fit = _fit(90)
    fc = forecast_followers(fit, {"original": 3.0}, _sampler(fit), seed=9, scenarios=None)
    frozen = FollowerForecast.from_dict(json.loads(json.dumps(fc.to_dict())))
    observed = int(fc.quantiles[7]["q50"])
    s = score_follower_forecast(
        frozen.draws[7], observed, horizon_days=7, followers_at_forecast=frozen.followers_now
    )
    assert s.outcome == "as_expected" and 0.3 < s.pit_mid < 0.7


def test_simulator_nb_matches_moments() -> None:
    rng = np.random.default_rng(0)
    x = np.array([nb(rng, 10.0, 2.0) for _ in range(20000)])
    assert x.mean() == pytest.approx(10.0, rel=0.03)
    assert x.var() == pytest.approx(10.0 + 100.0 / 2.0, rel=0.08)
