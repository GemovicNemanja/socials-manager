"""Experiment statistics (MODEL §6) and the M2 acceptance criteria (IMPLEMENTATION_PLAN §9).

Simulation tests are replicated. Those with >= 1,000 replicates per cell that take minutes are
marked ``slow`` (excluded from the default ``pytest -q``; run by ``scripts/check.sh`` in full mode
and by ``pytest -m slow``); each has a smaller-replicate version in the default run with a
tolerance widened by its Monte Carlo error. Seeds are fixed, so every run is reproducible.
"""

import math
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
from scipy import stats as sps

from socialmgr.experiments import simulate as sim
from socialmgr.experiments.stats import (
    ARM_HARM_THRESHOLD,
    DELTA,
    OUTCOMES,
    ELond,
    FactorPosterior,
    Observation,
    analyze,
    arm_harm_bound,
    assign_block,
    cs_upper_bound,
    cs_upper_from_stats,
    decide,
    decision_codes,
    decision_looks,
    e_value_from_p,
    msprt_e_value,
    power_preview,
    randomization_test,
    tau_prior_cov,
)

START = date(2026, 1, 5)


def make_obs(
    rng: np.random.Generator,
    n_days: int,
    *,
    tau: float = 0.0,
    ppd: int = 3,
    factors: tuple[str, ...] = ("A",),
    resid_sd: float = 0.92,
    forecast_sd: float = 0.6,
    slots: bool = True,
) -> list[Observation]:
    obs: list[Observation] = []
    prev = None
    for d in range(n_days):
        keys = [f"{d}-{j}" for j in range(ppd)]
        arms = assign_block(keys, list(factors), rng)
        for j, k in enumerate(keys):
            lf = 6.0 + rng.normal(0, forecast_sd)
            ly = lf + tau * arms[k][factors[0]] + rng.normal(0, resid_sd)
            slot = str(j) if slots else None
            obs.append(Observation(k, str(d), START + timedelta(days=d), ly, lf, arms[k], prev, slot=slot))
            prev = arms[k]
    return obs


# ---------------------------------------------------------------- assignment (MODEL §6.1)


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5, 6, 7])
def test_assign_block_treats_floor_or_ceil_per_factor(k: int) -> None:
    rng = np.random.default_rng(k)
    keys = [f"s{i}" for i in range(k)]
    counts: Counter[int] = Counter()
    for _ in range(2000):
        out = assign_block(keys, ["A", "B", "C"], rng)
        assert set(out) == set(keys)
        for f in ("A", "B", "C"):
            n_t = sum(out[s][f] for s in keys)
            assert n_t in {k // 2, (k + 1) // 2}
            counts[n_t] += 1
    if k % 2:  # the extra slot goes to a fair coin
        share = counts[(k + 1) // 2] / sum(counts.values())
        assert abs(share - 0.5) < 4 * math.sqrt(0.25 / 6000)


def test_assign_block_slots_have_pi_half_and_factors_are_independent() -> None:
    rng = np.random.default_rng(11)
    keys = ["a", "b", "c"]
    n = 8000
    joint = np.zeros((3, 2, 2))
    for _ in range(n):
        out = assign_block(keys, ["A", "B"], rng)
        for i, s in enumerate(keys):
            joint[i, out[s]["A"], out[s]["B"]] += 1
    for i in range(3):
        assert abs(joint[i, 1, :].sum() / n - 0.5) < 0.02  # pi = 0.5 per slot
        # chi-square test of independence of the two factors' arms on the same slot
        _, p, _, _ = sps.chi2_contingency(joint[i])
        assert p > 0.001


def test_assign_block_is_reproducible_from_the_seed() -> None:
    keys = [f"s{i}" for i in range(5)]
    a = assign_block(keys, ["A", "B"], np.random.default_rng(42))
    b = assign_block(keys, ["A", "B"], np.random.default_rng(42))
    c = assign_block(keys, ["A", "B"], np.random.default_rng(43))
    assert a == b
    assert any(assign_block(keys, ["A", "B"], np.random.default_rng(s)) != a for s in range(43, 53))
    assert isinstance(c, dict)
    with pytest.raises(ValueError):
        assign_block(["x", "x"], ["A"], np.random.default_rng(0))


def test_vectorized_assignments_match_assign_block() -> None:
    a = sim.draw_block_assignments(np.random.default_rng(1), 20000, [3, 2, 1])
    assert a.shape == (20000, 6)
    first = a[:, :3].sum(axis=1)
    assert set(np.unique(first)) == {1.0, 2.0}
    assert abs(np.mean(first == 2) - 0.5) < 0.02
    assert np.all(a[:, 3:5].sum(axis=1) == 1)
    assert abs(a[:, 5].mean() - 0.5) < 0.02
    assert np.allclose(a[:, :3].mean(axis=0), 0.5, atol=0.02)


# ---------------------------------------------------------------- analysis (MODEL §6.2)


def test_tau_prior_has_marginal_sd_s0_and_shares_the_family_mean() -> None:
    mean, cov = tau_prior_cov(["A", "B"])
    assert np.allclose(mean, 0)
    assert np.allclose(np.sqrt(np.diag(cov)), 0.2)
    assert cov[0, 1] == 0  # separate families by default
    _, cov2 = tau_prior_cov(["A", "B"], families={"A": "hook", "B": "hook"})
    assert np.allclose(np.sqrt(np.diag(cov2)), 0.2)
    assert math.isclose(cov2[0, 1], 0.2**2 - 0.1**2)
    mean3, cov3 = tau_prior_cov(["A"], family_priors={"A": (0.1, 0.05)})
    assert mean3[0] == 0.1 and math.isclose(cov3[0, 0], 0.05**2 + 0.1**2)


def test_analyze_is_reproducible_fast_and_recovers_a_known_effect() -> None:
    rng = np.random.default_rng(5)
    obs = make_obs(rng, 67, tau=math.log(2.0), factors=("A", "B"))  # n = 201
    t0 = time.perf_counter()
    post = analyze(obs, ["A", "B"], seed=1)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0  # ~0.5 s when the machine is idle
    again = analyze(obs, ["A", "B"], seed=1)
    assert np.array_equal(post["A"].draws, again["A"].draws)
    a, b = post["A"], post["B"]
    assert len(a.draws) == 4000
    assert a.n_treated + a.n_control == 201
    # the skeptical prior shrinks log 2 a little; the truth sits within the 95% interval
    lo, hi = a.interval(0.95)
    assert lo < math.log(2.0) < hi and a.mean > 0.4
    lo_b, hi_b = b.interval(0.95)
    assert lo_b < 0 < hi_b
    assert a.carryover_mean is not None and abs(a.carryover_mean) < 3 * (a.carryover_sd or 1)
    # draws are close to independent (one joint draw of all coefficients per sweep)
    d = a.draws
    assert abs(np.corrcoef(d[:-1], d[1:])[0, 1]) < 0.2


def test_analyze_without_contrast_returns_the_prior() -> None:
    rng = np.random.default_rng(2)
    obs = [Observation(o.slot_key, o.block, o.day, o.log_y, o.log_f, {"A": 0}) for o in make_obs(rng, 10)]
    post = analyze(obs, ["A"], seed=3)["A"]
    assert post.n_treated == 0
    assert abs(post.mean) < 0.02 and abs(post.sd - 0.2) < 0.02


def test_analyze_t_likelihood_resists_an_outlier() -> None:
    rng = np.random.default_rng(8)
    obs = make_obs(rng, 28, tau=0.0, slots=False)
    i = next(j for j, o in enumerate(obs) if o.arms["A"] == 1)
    o = obs[i]
    obs[i] = Observation(o.slot_key, o.block, o.day, o.log_y + 8.0, o.log_f, o.arms, o.prev_arms)
    post = analyze(obs, ["A"], seed=4)["A"]
    # a normal model would move the treated mean by ~8/42 = 0.19 (before shrinkage); the t barely moves
    clean = analyze(make_obs(np.random.default_rng(8), 28, tau=0.0, slots=False), ["A"], seed=4)["A"]
    assert abs(post.mean - clean.mean) < 0.06


def test_analyze_handles_one_post_a_day_in_two_day_pairs() -> None:
    rng = np.random.default_rng(3)
    obs = []
    for d in range(28):
        if d % 2 == 0:
            arms = assign_block([f"{d}", f"{d + 1}"], ["A"], rng)
        k = f"{d}"
        lf = 6 + rng.normal(0, 0.6)
        obs.append(
            Observation(k, str(d // 2), START + timedelta(days=d), lf + rng.normal(0, 0.9), lf, arms[k])
        )
    post = analyze(obs, ["A"], seed=1)["A"]
    assert post.n_treated == 14 and post.sd < 0.2


# ---------------------------------------------------------------- decision rule (MODEL §6.3)


def posterior(mean: float, sd: float, seed: int = 0) -> FactorPosterior:
    draws = np.random.default_rng(seed).normal(mean, sd, 20000)
    return FactorPosterior("A", mean, sd, draws, 42, 42)


def test_decide_follows_the_rule_table() -> None:
    assert decide(posterior(0.40, 0.12), at_horizon=False).outcome == "adopt"
    # P(theta > delta) >= 0.8 but expected loss >= 0.01: no adoption
    assert decide(posterior(0.35, 0.30), at_horizon=False).outcome == "continue"
    assert decide(posterior(-0.30, 0.2), at_horizon=False).outcome == "reject"
    assert decide(posterior(0.0, 0.15), at_horizon=False).outcome == "continue"
    assert decide(posterior(-0.05, 0.15), at_horizon=True).outcome == "reject"  # P(theta > delta) < 0.2
    near = decide(posterior(0.10, 0.12), at_horizon=True)
    assert near.outcome == "near_threshold" and "extension" in near.reason
    after = decide(posterior(0.10, 0.12), at_horizon=True, extended=True)
    assert after.outcome == "reject" and "extension" in after.reason


def test_decision_codes_agree_with_decide() -> None:
    rng = np.random.default_rng(0)
    for _ in range(200):
        post = posterior(float(rng.normal(0.1, 0.3)), float(rng.uniform(0.05, 0.4)), int(rng.integers(1e6)))
        for at_h in (False, True):
            lo, hi = post.interval(0.8)
            code = decision_codes(
                np.array([post.p_greater(DELTA)]),
                np.array([post.p_less(0.0)]),
                np.array([post.expected_loss]),
                np.array([lo]),
                np.array([hi]),
                at_h,
            )[0]
            assert OUTCOMES[code] == decide(post, at_h).outcome


def test_decision_looks_weekly_then_horizon_plus_48h() -> None:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    daily = [start + timedelta(days=d, hours=6) for d in range(40)]
    looks = decision_looks(start, 28, daily)
    assert [(lk.run_at - start).days for lk in looks] == [7, 14, 21, 30]
    assert [lk.final for lk in looks] == [False, False, False, True]
    assert [lk.week for lk in looks] == [1, 2, 3, 4]
    # a 9-day gap over two boundaries is one look; runs between boundaries are not looks
    gappy = [start + timedelta(days=d, hours=6) for d in (2, 5, 6, 15, 17, 26, 29, 31)]
    looks = decision_looks(start, 28, gappy)
    assert [((lk.run_at - start).days, lk.week, lk.final) for lk in looks] == [
        (15, 2, False),
        (26, 3, False),
        (31, 4, True),
    ]
    # the one 50% extension: weekly looks continue to week 5, final at 42 days + 48h
    looks = decision_looks(start, 42, [*daily, start + timedelta(days=44, hours=6)])
    assert [(lk.run_at - start).days for lk in looks] == [7, 14, 21, 28, 35, 44]
    with pytest.raises(ValueError):
        decision_looks(datetime(2026, 1, 5), 28, daily)


def test_look_sizes_match_the_batch_semantics() -> None:
    assert sim.look_sizes(4, 3, 0) == [21, 42, 63, 84]  # Simulation F
    assert sim.look_sizes(4, 3, 2) == [15, 36, 57, 84]  # posts younger than 48h are not mature


# ---------------------------------------------------------------- randomization test (MODEL §6.2)


def test_randomization_test_is_fast_reproducible_and_finds_a_real_effect() -> None:
    rng = np.random.default_rng(9)
    obs = make_obs(rng, 28, tau=math.log(2.5), factors=("A", "B"))
    t0 = time.perf_counter()
    p = randomization_test(obs, "A", seed=1)
    assert time.perf_counter() - t0 < 1.0
    assert p == randomization_test(obs, "A", seed=1)
    assert 1 / 10_000 <= p < 0.01
    assert randomization_test(obs, "B", seed=1) > 0.01


def test_aa_size_small() -> None:
    """Default-run version: 600 A/A experiments; size within 0.05 ± 3 Monte Carlo SE."""
    size = sim.simulate_aa_rejections(600, seed=21)
    assert abs(size - 0.05) < 3 * math.sqrt(0.05 * 0.95 / 600)


@pytest.mark.slow
def test_aa_size_is_between_4_and_6_percent() -> None:
    """M2 acceptance: A/A size of the randomization test at alpha = 0.05 is 0.04-0.06
    (4,000 experiments, 999 re-draws each; Monte Carlo SE 0.0034)."""
    size = sim.simulate_aa_rejections(4000, seed=22)
    assert 0.04 <= size <= 0.06


# ---------------------------------------------------------------- e-LOND (MODEL §6.7)


def test_elond_levels_and_bookkeeping() -> None:
    el = ELond(alpha=0.10)
    # alpha_t = alpha * gamma_t * (R_{t-1} + 1), gamma_t = 1/(t(t+1))
    assert math.isclose(el.level(), 0.10 * 0.5)
    assert el.test(25.0, "E01") is True  # 25 >= 1/0.05 = 20
    assert math.isclose(el.level(), 0.10 * (1 / 6) * 2)
    assert el.test(10.0, "E02") is False  # needs 1/0.0333 = 30
    assert el.test(1e9, "E03") is True
    assert (el.tests, el.rejections) == (3, 2)
    assert [h["rejected"] for h in el.history] == [True, False, True]
    assert math.isclose(el.history[2]["alpha_t"], 0.10 * (1 / 12) * 2)
    # round trip, then both copies keep making the same decisions
    copy = ELond.from_dict(el.to_dict())
    assert copy == el
    for e in (3.0, 400.0, 50.0, 1e4):
        assert copy.test(e) == el.test(e)
    assert copy.to_dict() == el.to_dict()


def test_elond_gamma_sequences_sum_to_at_most_one_and_legacy_streams_keep_theirs() -> None:
    for kind in ("1/(t(t+1))", "6/(pi^2 t^2)"):
        el = ELond(gamma_sequence=kind)  # type: ignore[arg-type]
        assert sum(el.gamma(t) for t in range(1, 200_000)) <= 1.0
    legacy = ELond.from_dict({"alpha": 0.1, "tests": 0, "rejections": 0, "history": []})
    assert legacy.gamma_sequence == "6/(pi^2 t^2)"
    assert math.isclose(legacy.level(), 0.1 * 6 / math.pi**2)


def test_e_values() -> None:
    assert math.isclose(e_value_from_p(0.25), 1.0)
    assert math.isclose(e_value_from_p(1e-4), 50.0)
    assert msprt_e_value(0.0, 0.2) < 1
    assert msprt_e_value(0.8, 0.2) > 50
    # the mixture likelihood ratio has mean 1 under H0
    z = np.linspace(-10, 10, 4001)
    vals = np.array([msprt_e_value(float(x) * 0.2, 0.2) for x in z])
    assert abs(np.trapezoid(vals * sps.norm.pdf(z), z) - 1) < 1e-3


@pytest.mark.parametrize(("rho", "null_share"), [(0.0, 0.7), (0.6, 0.7), (0.0, 1.0)])
def test_elond_controls_fdr(rho: float, null_share: float) -> None:
    """FDR <= 0.10 over 1,000 streams of 40 calibrated e-values, independent or sharing a factor."""
    fdr, n_rej = sim.simulate_elond_fdr(1000, seed=31, rho=rho, null_share=null_share, effect_z=5.0)
    assert fdr <= 0.10
    if null_share < 1:
        assert n_rej > 1  # it does reject strong effects


def test_elond_controls_fdr_with_worst_case_null_e_values() -> None:
    """Null e-values that put all their mass exactly on the rejection threshold
    (e = 1/alpha_t with probability alpha_t, else 0; mean 1): FDR stays <= alpha (4,000 streams)."""
    rng = np.random.default_rng(32)
    fdp = []
    for _ in range(4000):
        el = ELond(alpha=0.10)
        is_null = rng.random(30) < 0.5
        v = 0
        for t in range(30):
            level = el.level()
            e = (1 / level if rng.random() < level else 0.0) if is_null[t] else 1e6
            v += int(el.test(e) and is_null[t])
        fdp.append(v / max(el.rejections, 1))
    assert np.mean(fdp) <= 0.10 + 3 * np.std(fdp) / math.sqrt(len(fdp))


# ---------------------------------------------------------------- guardrails (MODEL §6.4)


def test_cs_upper_bound_basics() -> None:
    assert cs_upper_bound([0.1, 0.2]) == math.inf
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1, 50)
    u = cs_upper_bound(list(x))
    assert x.mean() < u < x.mean() + 1.5
    assert cs_upper_bound(list(x), alpha=0.01) > u  # wider at smaller alpha
    assert cs_upper_bound(list(rng.normal(0, 1, 500))) < u  # narrows with data
    m, ss = float(x.mean()), float(np.sum((x - x.mean()) ** 2))
    assert math.isclose(float(cs_upper_from_stats(50.0, m, ss, 0.05, 1.0)), u)


@pytest.mark.parametrize("dist", ["normal", "t2", "skew-differences"])
def test_cs_is_valid_at_every_look(dist: str) -> None:
    """P(the true mean ever exceeds the upper bound over 150 looks) <= alpha for conditionally
    symmetric increments of any spread (1,000 replicate sequences)."""
    rng = np.random.default_rng(41)
    r, t_max, mu = 1000, 150, -0.3
    if dist == "normal":
        x = rng.normal(mu, 1.0, (r, t_max))
    elif dist == "t2":
        x = mu + rng.standard_t(2, (r, t_max))
    else:  # paired differences of skewed, exchangeable noise are symmetric
        x = mu + rng.lognormal(0, 1, (r, t_max)) - rng.lognormal(0, 1, (r, t_max))
    t = np.arange(1, t_max + 1, dtype=float)
    mean = np.cumsum(x, axis=1) / t
    ss = np.maximum(np.cumsum(x**2, axis=1) - t * mean**2, 0)
    upper = cs_upper_from_stats(t, mean, ss, 0.05, 8.0)
    assert (upper < mu).any(axis=1).mean() <= 0.05


def test_arm_harm_bound_uses_block_contrasts_or_pairs() -> None:
    rng = np.random.default_rng(2)
    t_vals: list[float] = []
    c_vals: list[float] = []
    t_blk: list[str] = []
    c_blk: list[str] = []
    contrasts = []
    for b in range(20):
        a = assign_block(["x", "y", "z"], ["A"], rng)
        v = {s: float(rng.normal(-0.7 * a[s]["A"], 1)) for s in a}
        tr = [v[s] for s in a if a[s]["A"]]
        co = [v[s] for s in a if not a[s]["A"]]
        t_vals += tr
        c_vals += co
        t_blk += [str(b)] * len(tr)
        c_blk += [str(b)] * len(co)
        contrasts.append(float(np.mean(tr) - np.mean(co)))
    blocked = arm_harm_bound(t_vals, c_vals, treated_blocks=t_blk, control_blocks=c_blk)
    assert math.isclose(blocked, cs_upper_bound(contrasts, 0.05, 8.0))
    m = min(len(t_vals), len(c_vals))
    paired = arm_harm_bound(t_vals, c_vals)
    assert math.isclose(paired, cs_upper_bound([t_vals[j] - c_vals[j] for j in range(m)], 0.05, 8.0))
    assert arm_harm_bound([0.1, 0.2], [0.0, 0.1, 0.3]) == math.inf
    with pytest.raises(ValueError):
        arm_harm_bound(t_vals, c_vals, treated_blocks=t_blk)


@pytest.mark.parametrize("log_sd", [0.9, 1.0, 1.1])
@pytest.mark.parametrize("look_every_days", [1, 7])
def test_arm_harm_trips_in_at_most_5_percent_of_null_experiments(log_sd: float, look_every_days: int) -> None:
    """M2 acceptance: calibrated null (predictive log-sd 0.9-1.1), 4-week experiment, looks at
    every daily run or weekly: the guardrail trips in <= 5% of 2,000 experiments."""
    res = sim.simulate_arm_harm(1.0, 2000, seed=51, log_sd=log_sd, look_every_days=look_every_days)
    assert res.trip_rate <= 0.05
    # even at the guardrail's own boundary (a true 0.8x arm) it stays within alpha
    edge = sim.simulate_arm_harm(0.8, 2000, seed=52, log_sd=log_sd, look_every_days=look_every_days)
    assert edge.trip_rate <= 0.05


def test_arm_harm_trips_under_real_harm() -> None:
    """A real 0.5x harm trips the guardrail in about a fifth of 4-week experiments (daily looks)
    and over half by 8 weeks; a 0.3x harm in most (2,000 experiments each)."""
    four = sim.simulate_arm_harm(0.5, 2000, seed=53, log_sd=1.0)
    eight = sim.simulate_arm_harm(0.5, 2000, seed=54, log_sd=1.0, weeks=8)
    severe = sim.simulate_arm_harm(0.3, 2000, seed=55, log_sd=1.0)
    assert 0.15 <= four.trip_rate <= 0.30
    assert eight.trip_rate >= 0.45
    assert severe.trip_rate >= 0.80


def test_old_three_consecutive_rule_tripped_29_to_44_percent() -> None:
    """MODEL §6.4's reason for the change: under a calibrated null, '3 consecutive treated posts
    below 0.5x' trips in 29-44% of 4-week experiments (42 treated posts; 20,000 replicates)."""
    low = sim.old_rule_trip_rate(20000, seed=61, log_sd=0.9)
    high = sim.old_rule_trip_rate(20000, seed=62, log_sd=1.1)
    assert abs(low - 0.29) < 0.015 and abs(high - 0.44) < 0.015
    assert math.log(0.8) == ARM_HARM_THRESHOLD


# ---------------------------------------------------------------- power preview (MODEL §6.8)

# n: {R²: (power at 1.25x, 1.5x, 2x, MDE or None)}
POWER_TABLE = {
    42: {0.0: (0.09, 0.21, 0.50, 2.59), 0.3: (0.11, 0.28, 0.65, 2.22), 0.5: (0.15, 0.36, 0.80, 1.96)},
    84: {0.0: (0.14, 0.38, 0.80, 1.96), 0.3: (0.21, 0.50, 0.92, 1.76), 0.5: (0.25, 0.65, 0.98, 1.61)},
    168: {0.0: (0.26, 0.66, 0.98, 1.61), 0.3: (0.35, 0.81, 1.0, 1.49), 0.5: (0.46, 0.92, 1.0, 1.40)},
    28: {0.0: (0.08, 0.15, 0.37, None), 0.3: (0.09, 0.20, 0.46, None), 0.5: (0.12, 0.26, 0.61, None)},
    56: {0.0: (0.10, 0.27, 0.64, None), 0.3: (0.15, 0.37, 0.79, None), 0.5: (0.17, 0.48, 0.90, None)},
}


@pytest.mark.parametrize("n", list(POWER_TABLE))
def test_power_preview_matches_the_model_table(n: int) -> None:
    for r2, (p125, p15, p2, mde) in POWER_TABLE[n].items():
        pp = power_preview(n, sigma=1.1, r2=r2)
        assert abs(pp.power["1.25x"] - p125) <= 0.03
        assert abs(pp.power["1.5x"] - p15) <= 0.03
        assert abs(pp.power["2x"] - p2) <= 0.03
        if mde is not None:
            assert abs(pp.mde_ratio_80 - mde) <= 0.03


# ---------------------------------------------------------------- decision rule OCs (MODEL §6.3)

# Simulation F (MODEL §6.3; research notes §4.7): P(adopt) at each true lift, 4 weeks, weekly looks
SIMULATION_F = {1.0: 0.023, 0.8: 0.001, 1.25: 0.177, 1.5: 0.484, 2.0: 0.913}


@pytest.mark.parametrize("lift", [1.0, 2.0])
def test_decision_rule_operating_characteristics_small(lift: float) -> None:
    """Default-run version: 200 experiments; within ±3 points plus 2 Monte Carlo SE."""
    res = sim.simulate_decision_rule(lift, 200, seed=71 + int(lift * 10))
    target = SIMULATION_F[lift]
    assert abs(res.p_adopt - target) <= 0.03 + 2 * math.sqrt(max(target * (1 - target), 0.03) / 200)


@pytest.mark.slow
@pytest.mark.parametrize("lift", list(SIMULATION_F))
def test_decision_rule_reproduces_simulation_f(lift: float) -> None:
    """M2 acceptance: the full §6.2 analysis (Gibbs, t errors, beta, Lin interaction, carryover,
    day effect) and the §6.3 rule at weekly looks, 3 posts/day for 4 weeks (n = 84), log f
    explaining R² = 0.3 of a total log-sd of 1.1: P(adopt) within ±3 points of Simulation F over
    1,000 experiments (Monte Carlo SE <= 0.016)."""
    res = sim.simulate_decision_rule(lift, 1000, seed=81 + int(lift * 100))
    assert abs(res.p_adopt - SIMULATION_F[lift]) <= 0.03


@pytest.mark.slow
def test_false_adoption_in_batch_mode_with_maturity_lag() -> None:
    """Batch-mode looks see only posts at least 48h old (15/36/57 posts, final 84): false
    adoption at a true null stays within ±3 points of 2.3% (1,000 experiments)."""
    res = sim.simulate_decision_rule(1.0, 1000, seed=91, maturity_lag_days=2)
    assert abs(res.p_adopt - SIMULATION_F[1.0]) <= 0.03
