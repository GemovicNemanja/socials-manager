"""Drift detectors (MODEL §4.3, plan §6, M3 acceptance): ARL solver, D1 charts, diagnostics, D2, D3.

The M3 criterion owned here: "each detector's false-alarm run length matches its target within ±10% over
≥ 1,000 NB-simulated replicates". In-control posts are drawn from NB2 at several μ and φ, the randomized PIT
is taken under the true predictive, and each chart runs until it alarms. The slow tests (``pytest -m slow``)
use 4,000 replicates per configuration (36,000 per chart); the fast ones use fewer with a looser
tolerance. All seeded.
"""

import itertools
import json
from collections.abc import Callable

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy import special, stats

from socialmgr.modelling.monitors import (
    LOCATION_H,
    SCALE_REFERENCE_H,
    AciState,
    CusumChart,
    EwmaChart,
    GoalInputs,
    MonitorReport,
    MonitorState,
    aci_recursion,
    berkowitz_test,
    binomial_band,
    chart_input,
    check_goal,
    coverage_check,
    cusum_arl,
    cusum_recursion,
    diagnostics_due,
    ewma_arl,
    ewma_chart,
    ewma_limit_for_arl,
    follower_chart,
    initial_state,
    location_chart,
    pit_to_z,
    pit_uniformity_test,
    pits_to_z,
    poisson_binomial_pmf,
    poisson_binomial_test,
    run_diagnostics,
    run_monitors,
    scale_chart,
    scale_thresholds,
    threshold_for_arl,
    two_sided_arl,
)

Rng = np.random.Generator
F64 = NDArray[np.float64]

PHIS = (1.5, 3.0, 8.0)
POST_MUS = (20.0, 300.0, 5000.0)
FOLLOWER_BASE_MUS = (1.0, 10.0, 60.0)  # gains per day; per interval × G


# ---------------------------------------------------------------- simulation helpers


def nb_randomized_pit(rng: Rng, mu_true: float, mu_pred: float, phi: float, size: int) -> F64:
    """y ~ NB2(mu_true, φ); randomized PIT under the predictive NB2(mu_pred, φ)."""
    y = rng.negative_binomial(phi, phi / (phi + mu_true), size)
    p_pred = phi / (phi + mu_pred)
    lo = stats.nbinom.cdf(y - 1, phi, p_pred)
    pm = stats.nbinom.pmf(y, phi, p_pred)
    return np.asarray(lo + rng.uniform(size=size) * pm, dtype=np.float64)


PitSource = Callable[[Rng, int], F64]


def nb_source(mu: float, phi: float, drop: float = 1.0) -> PitSource:
    return lambda rng, size: nb_randomized_pit(rng, mu * drop, mu, phi, size)


def cusum_run_lengths(
    rng: Rng, reps: int, chart: CusumChart, source: PitSource, max_steps: int = 100_000
) -> NDArray[np.int64]:
    """Run ``reps`` independent copies of ``chart`` (vectorised) until each alarms; zero start."""
    su, sl = np.zeros(reps), np.zeros(reps)
    rl = np.zeros(reps, dtype=np.int64)
    alive = np.arange(reps)
    for step in range(1, max_steps + 1):
        z = pits_to_z(source(rng, alive.size))
        x = chart_input(z, chart.transform)
        su[alive], sl[alive] = cusum_recursion(su[alive], sl[alive], x, chart.k)
        hit = (su[alive] > chart.h_upper) | (sl[alive] > chart.h_lower)
        rl[alive[hit]] = step
        alive = alive[~hit]
        if alive.size == 0:
            return rl
    raise AssertionError("replicates still running at max_steps")


def aci_run_lengths(
    rng: Rng, reps: int, source: PitSource, max_steps: int = 1_000_000, censor: bool = False
) -> NDArray[np.int64]:
    """D3 run lengths to the first alarm; with ``censor``, replicates still running get max_steps + 1."""
    s = AciState()
    alpha = np.full(reps, s.alpha_t)
    streak = np.zeros(reps, dtype=np.int64)
    rl = np.zeros(reps, dtype=np.int64)
    alive = np.arange(reps)
    for step in range(1, max_steps + 1):
        a, _ = aci_recursion(alpha[alive], source(rng, alive.size), s.alpha, s.gamma, s.alpha_lo, s.alpha_hi)
        alpha[alive] = a
        streak[alive] = np.where(a < s.alarm_level, streak[alive] + 1, 0)
        hit = streak[alive] >= s.alarm_run
        rl[alive[hit]] = step
        alive = alive[~hit]
        if alive.size == 0:
            return rl
    if censor:
        rl[alive] = max_steps + 1
        return rl
    raise AssertionError("replicates still running at max_steps")


def check_arl0(chart: CusumChart, mus: tuple[float, ...], reps: int, tol: float, seed: int) -> list[float]:
    """Mean run length per (μ, φ) within ``tol`` of the target, and pooled within tol / 2."""
    means, pooled = [], []
    for i, (mu, phi) in enumerate(itertools.product(mus, PHIS)):
        rl = cusum_run_lengths(np.random.default_rng([seed, i]), reps, chart, nb_source(mu, phi))
        means.append(float(rl.mean()))
        pooled.append(rl)
        assert abs(rl.mean() / chart.target_arl0 - 1.0) <= tol, (chart.name, mu, phi, rl.mean())
    overall = float(np.concatenate(pooled).mean())
    assert abs(overall / chart.target_arl0 - 1.0) <= tol / 2, (chart.name, overall)
    return [*means, overall]


# ---------------------------------------------------------------- Brook-Evans ARL solver


def test_location_threshold_matches_model() -> None:
    # One side at ARL0 600 → h ≈ 7.62 (MODEL §4.3, "verified 2026-11-25").
    assert threshold_for_arl(600, 0.25) == pytest.approx(LOCATION_H, abs=0.015)
    assert cusum_arl(LOCATION_H, 0.25) == pytest.approx(600, rel=0.01)
    # Single-chart h = 5.61 ↔ ARL0 100 two-sided (each side at 200).
    one = cusum_arl(5.61, 0.25)
    assert two_sided_arl(one, cusum_arl(5.61, 0.25, side="lower")) == pytest.approx(100, rel=0.015)
    assert threshold_for_arl(200, 0.25) == pytest.approx(5.61, abs=0.015)


def test_scale_reference_thresholds_match_model() -> None:
    hu, hl = SCALE_REFERENCE_H
    assert cusum_arl(hu, 0.25, "chi2_1_minus_1", "upper") == pytest.approx(200, rel=0.015)
    assert cusum_arl(hl, 0.25, "chi2_1_minus_1", "lower") == pytest.approx(200, rel=0.015)
    solved = scale_thresholds(100)  # 200 each side
    assert solved[0] == pytest.approx(hu, abs=0.03)
    assert solved[1] == pytest.approx(hl, abs=0.03)


def test_deployed_scale_thresholds() -> None:
    hu, hl = scale_thresholds(300)
    assert hu == pytest.approx(16.08, abs=0.01)
    assert hl == pytest.approx(9.59, abs=0.01)
    assert cusum_arl(hu, 0.25, "chi2_1_minus_1", "upper") == pytest.approx(600, rel=1e-3)
    assert cusum_arl(hl, 0.25, "chi2_1_minus_1", "lower") == pytest.approx(600, rel=1e-3)


@pytest.mark.parametrize(
    ("dist", "side", "target"),
    [("normal", "upper", 66.7), ("normal", "lower", 450.0), ("chi2_1_minus_1", "lower", 90.0)],
)
def test_threshold_root_finder_inverts_the_arl(dist: str, side: str, target: float) -> None:
    h = threshold_for_arl(target, 0.25, dist, side)  # type: ignore[arg-type]
    assert cusum_arl(h, 0.25, dist, side) == pytest.approx(target, rel=1e-4)  # type: ignore[arg-type]


def test_solver_is_converged_in_states_and_accepts_a_general_cdf() -> None:
    coarse = cusum_arl(7.62, 0.25, n_states=400)
    fine = cusum_arl(7.62, 0.25, n_states=1600)
    assert coarse == pytest.approx(fine, rel=1e-3)

    def norm_cdf(x: F64) -> F64:
        return np.asarray(stats.norm.cdf(x), dtype=np.float64)

    assert cusum_arl(5.0, 0.5, norm_cdf, "lower") == pytest.approx(cusum_arl(5.0, 0.5), rel=1e-9)


def test_arl_from_a_head_start_is_shorter() -> None:
    zero = cusum_arl(LOCATION_H, 0.25)
    fir = cusum_arl(LOCATION_H, 0.25, start=LOCATION_H / 2)
    assert fir < zero
    # Out of control (a +0.5 SD shift, i.e. k = -0.25 on the shifted input) the head start pays off.
    assert cusum_arl(LOCATION_H, -0.25, start=LOCATION_H / 2) < 0.8 * cusum_arl(LOCATION_H, -0.25)


def test_ewma_reference_and_deployed_limits() -> None:
    assert ewma_arl(0.1, 2.148) == pytest.approx(100, rel=0.01)  # MODEL §4.3
    lim = ewma_limit_for_arl(300.0, 0.1)
    assert ewma_arl(0.1, lim) == pytest.approx(300, rel=1e-3)
    assert 2.5 < lim < 2.7


# ---------------------------------------------------------------- D1 charts


def test_pit_to_z_clips_away_from_zero_and_one() -> None:
    assert pit_to_z(0.5) == 0.0
    assert np.isfinite(pit_to_z(0.0)) and np.isfinite(pit_to_z(1.0))
    assert pit_to_z(0.0) == pytest.approx(special.ndtri(1e-6))
    assert pit_to_z(1.0, eps=1e-3) == pytest.approx(-special.ndtri(1e-3))
    np.testing.assert_allclose(pits_to_z([0.0, 0.5, 1.0]), [pit_to_z(0.0), 0.0, pit_to_z(1.0)])


def test_cusum_chart_follows_the_recursion_and_resets_with_a_head_start() -> None:
    rng = np.random.default_rng(7)
    z = np.concatenate([rng.normal(size=30), rng.normal(-1.5, 1.0, size=30)])
    chart = location_chart()
    su = sl = 0.0
    alarms = []
    for i, zi in enumerate(z, start=1):
        chart, alarm = chart.update(float(zi), armed=True)
        a, b = cusum_recursion(np.array(su), np.array(sl), np.array(zi), 0.25)
        su, sl = float(a), float(b)
        if su > LOCATION_H or sl > LOCATION_H:
            assert alarm is not None and alarm.index == i
            assert alarm.side == ("lower" if sl > su else "upper")
            su = sl = LOCATION_H / 2
            alarms.append(alarm)
        else:
            assert alarm is None
        assert (chart.s_upper, chart.s_lower) == pytest.approx((su, sl))
    assert alarms and all(a.side == "lower" for a in alarms)
    assert chart.alarm_count == len(alarms)
    assert chart.last_alarm_index == alarms[-1].index and chart.last_alarm_side == "lower"
    assert chart.n == z.size


def test_charts_do_nothing_while_disarmed() -> None:
    chart = scale_chart()
    nxt, alarm = chart.update(10.0, armed=False)
    assert alarm is None and nxt.n == 0 and nxt.s_upper == 0.0 and not nxt.armed
    nxt, alarm = nxt.update(10.0, armed=True)  # z² - 1 = 99 > h_upper
    assert alarm is not None and alarm.side == "upper" and nxt.armed
    assert nxt.s_upper == pytest.approx(nxt.h_upper / 2)


def test_follower_thresholds_follow_the_run_gap() -> None:
    hs = {g: follower_chart(g).h_upper for g in (1, 2, 3)}
    assert hs[1] == pytest.approx(threshold_for_arl(200, 0.25))  # ARL0 100 → each side 200
    assert hs[1] > hs[2] > hs[3]
    c = follower_chart(3)
    assert c.target_arl0 == pytest.approx(100 / 3)
    c = CusumChart.from_dict({**c.to_dict(), "s_lower": 3.5})
    c1 = c.with_gap(1.0)
    assert c1.gap_days == 1.0 and c1.h_upper == pytest.approx(hs[1]) and c1.s_lower == 3.5
    c3 = c1.with_gap(3.0)
    assert c3.h_upper == pytest.approx(hs[3]) and c3.s_lower == pytest.approx(min(3.5, hs[3]))


def test_ewma_chart_alarms_on_a_shift_and_resets() -> None:
    chart = ewma_chart()
    assert chart.limit == 2.148 and ewma_chart(300).limit > 2.148
    alarm = None
    for _ in range(100):
        chart, alarm = chart.update(-2.0, armed=True)
        if alarm:
            break
    assert alarm is not None and alarm.side == "lower" and chart.value == 0.0 and chart.n == 0
    exact = EwmaChart(exact_limits=True)
    assert exact.control_limit(1) < exact.control_limit(100) == pytest.approx(chart.control_limit())


def test_states_round_trip_through_json() -> None:
    rng = np.random.default_rng(11)
    state = initial_state(2.0)
    report = None
    for _ in range(4):
        state, report = run_monitors(
            state,
            post_pits=list(rng.uniform(size=9)),
            follower_pits=[float(rng.uniform())],
            matured_owner_posts=25,
            gap_days=2.0,
        )
    assert report is not None
    for obj in (state, state.location, state.scale, state.follower, state.aci, ewma_chart(), report):
        restored = type(obj).from_dict(json.loads(json.dumps(obj.to_dict())))
        assert restored == obj
    assert MonitorState.from_dict(json.loads(json.dumps(state.to_dict()))) == state
    assert MonitorReport.from_dict(json.loads(json.dumps(report.to_dict()))) == report


# ---------------------------------------------------------------- diagnostics


def test_binomial_bands() -> None:
    assert binomial_band(60, 0.8) == pytest.approx((0.70, 0.90), abs=1e-9)
    lo, hi = binomial_band(63, 0.5)
    assert lo < 0.5 < hi and hi - lo < 0.27


def test_coverage_check() -> None:
    pit = np.linspace(0.005, 0.995, 100)
    c80, c50 = coverage_check(pit, 0.8), coverage_check(pit, 0.5)
    assert c80.hits == 80 and c80.inside
    assert c50.hits == 50 and c50.inside
    assert not coverage_check(np.full(60, 0.95), 0.8).inside


def test_pit_uniformity_test_agrees_with_scipy() -> None:
    pit = np.random.default_rng(5).uniform(size=63)
    res = pit_uniformity_test(pit)
    counts = np.histogram(pit, bins=10, range=(0, 1))[0]
    assert res.counts == tuple(counts)
    assert res.p_value == pytest.approx(stats.chisquare(counts).pvalue)
    assert pit_uniformity_test(np.random.default_rng(5).beta(3, 1, size=63)).p_value < 0.01


def test_berkowitz_detects_bias_scale_and_autocorrelation() -> None:
    rng = np.random.default_rng(13)
    good = berkowitz_test(rng.normal(size=63))
    assert good.p_value > 0.01 and abs(good.mu) < 0.4 and abs(good.rho) < 0.35
    assert berkowitz_test(rng.normal(0.8, 1.0, size=63)).p_value < 0.01
    assert berkowitz_test(rng.normal(0.0, 1.8, size=63)).p_value < 0.01
    e = rng.normal(size=63)
    ar = np.empty(63)
    ar[0] = e[0]
    for t in range(1, 63):
        ar[t] = 0.7 * ar[t - 1] + np.sqrt(1 - 0.49) * e[t]
    res = berkowitz_test(ar)
    assert res.p_value < 0.01 and res.rho == pytest.approx(0.7, abs=0.2)


def test_berkowitz_size_is_near_nominal() -> None:
    rng = np.random.default_rng(17)
    p = np.array([berkowitz_test(rng.normal(size=63)).p_value for _ in range(300)])
    assert 0.02 <= np.mean(p < 0.05) <= 0.10


def test_diagnostics_every_21_over_the_last_63() -> None:
    assert not diagnostics_due(20, 0) and diagnostics_due(21, 0) and not diagnostics_due(41, 21)
    assert diagnostics_due(45, 21)  # a batch can jump past the multiple
    pit = np.random.default_rng(19).uniform(size=100)
    d = run_diagnostics(pit, at_post=100)
    assert d.n == 63 and d.uniformity.n == 63 and d.coverage_80.n == 63
    assert d.passed


# ---------------------------------------------------------------- D3 (ACI)


def test_aci_class_matches_the_recursion() -> None:
    rng = np.random.default_rng(23)
    s = AciState()
    alpha = np.float64(0.2)
    for pit in rng.uniform(size=200):
        s, _ = s.update(float(pit), armed=True)
        a, _ = aci_recursion(np.array(alpha), np.array(pit), 0.2, 0.05, 0.01, 0.6)
        alpha = np.float64(a)
        assert s.alpha_t == pytest.approx(float(alpha))
    assert 0.01 <= s.alpha_t <= 0.6


def test_aci_alarms_once_per_streak_when_intervals_keep_missing() -> None:
    s = AciState()
    alarms = []
    for i in range(80):
        pit = 0.001 if i % 2 == 0 else 0.5  # half the posts far in the tail: far over the 20% miss rate
        s, alarm = s.update(pit, armed=True)
        if alarm:
            alarms.append(alarm)
    assert len(alarms) == 1 and s.in_alarm
    assert alarms[0].statistic < 0.10
    lo, hi = s.interval_levels
    assert lo < 0.05 and hi > 0.95
    assert s.alpha_t >= 0.01


def test_aci_in_control_rarely_alarms() -> None:
    rl = aci_run_lengths(np.random.default_rng(29), 1000, nb_source(30.0, 3.0), max_steps=300, censor=True)
    assert np.mean(rl <= 300) < 0.05


# ---------------------------------------------------------------- D2 (goal is wrong)


def test_poisson_binomial_pmf_and_test() -> None:
    p = np.array([0.1, 0.5, 0.9, 0.3])
    pmf = poisson_binomial_pmf(p)
    brute = np.zeros(5)
    for bits in itertools.product([0, 1], repeat=4):
        brute[sum(bits)] += np.prod([pi if b else 1 - pi for pi, b in zip(p, bits, strict=True)])
    np.testing.assert_allclose(pmf, brute)
    for hits in (0, 3, 9, 15):
        expect = stats.binomtest(hits, 30, 0.3).pvalue
        assert poisson_binomial_test(hits, np.full(30, 0.3)) == pytest.approx(expect, rel=1e-6)


def _goal_world(rng: Rng, n: int = 40, mu: float = 200.0, phi: float = 3.0) -> tuple[F64, F64]:
    mus = mu * np.exp(rng.normal(0, 0.3, size=n))
    draws = np.stack([rng.negative_binomial(phi, phi / (phi + m), 2000) for m in mus]).astype(float)
    y = np.array([rng.negative_binomial(phi, phi / (phi + m)) for m in mus], dtype=float)
    return draws, y


def test_goal_check_flags_a_goal_that_is_too_hard() -> None:
    draws, y = _goal_world(np.random.default_rng(31))
    goal = float(np.quantile(draws[-30:], 0.97))
    res = check_goal(draws, y, goal, d1_quiet=True)
    assert res.status == "too_hard" and res.proposed and res.pi_bar is not None and res.pi_bar < 0.10
    assert res.p_value is not None and res.p_value < 0.05
    pooled = np.round(draws[-30:]).ravel()
    assert res.suggestion == pytest.approx(np.quantile(pooled, 0.65))
    assert res.range_lo == pytest.approx(np.quantile(pooled, 0.60))
    assert res.range_hi == pytest.approx(np.quantile(pooled, 0.70))
    assert res.suggestion_percentile == 0.65


def test_goal_check_flags_a_goal_that_is_too_easy() -> None:
    draws, y = _goal_world(np.random.default_rng(37))
    res = check_goal(draws, y, float(np.quantile(draws[-30:], 0.03)), d1_quiet=True)
    assert res.status == "too_easy" and res.pi_bar is not None and res.pi_bar > 0.90
    pooled = np.round(draws[-30:]).ravel()
    assert res.suggestion == pytest.approx(np.quantile(pooled, 0.75))


def test_goal_check_needs_the_outcomes_to_agree() -> None:
    draws, _ = _goal_world(np.random.default_rng(41))
    goal = float(np.quantile(draws[-30:], 0.97))
    # The model says the goal is rarely hit, but the outcomes clear it half the time: no proposal.
    y = np.where(np.arange(40) % 2 == 0, goal * 2, 0.0)
    res = check_goal(draws, y, goal, d1_quiet=True)
    assert res.status == "not_significant" and not res.proposed
    assert res.calibration_p is not None and res.calibration_p < 0.001


def test_goal_check_gating() -> None:
    draws, y = _goal_world(np.random.default_rng(43))
    median = float(np.quantile(draws[-30:], 0.5))
    assert check_goal(draws, y, median, d1_quiet=True).status == "goal_ok"
    assert check_goal(draws, y, median, d1_quiet=False).status == "skipped_d1_alarm"
    assert check_goal(draws[:20], y[:20], median, d1_quiet=True).status == "insufficient_posts"


def test_goal_check_size_under_calibration() -> None:
    # When the model is right the hit-count test should not be what makes proposals: check its level
    # against a typical-level goal (the mixture median), where the null holds exactly.
    rng = np.random.default_rng(47)
    rejections = 0
    for _ in range(200):
        draws, y = _goal_world(rng, n=30)
        res = check_goal(draws, y, float(np.quantile(np.round(draws), 0.5)), d1_quiet=True)
        rejections += int(res.p_value is not None and res.p_value < 0.05)
    assert rejections / 200 <= 0.08


# ---------------------------------------------------------------- one run of everything


def test_run_monitors_arms_diagnoses_and_gates_d2() -> None:
    rng = np.random.default_rng(53)
    state = initial_state(3.0)
    # Before 20 matured posts nothing runs, but PITs are kept for later diagnostics.
    state, report = run_monitors(
        state, post_pits=list(rng.uniform(size=10)), follower_pits=[0.4], matured_owner_posts=10, gap_days=3.0
    )
    assert not report.armed and state.location.n == 0 and state.follower.n == 0 and state.posts_seen == 10
    assert report.diagnostics is None and len(state.recent_pit) == 10
    # 21 posts seen and armed: diagnostics are due.
    state, report = run_monitors(
        state, post_pits=list(rng.uniform(size=11)), follower_pits=[0.6], matured_owner_posts=21, gap_days=3.0
    )
    assert report.armed and state.location.n == 11 and state.aci.n == 11 and state.follower.n == 1
    assert report.diagnostics is not None and report.diagnostics.n == 21 and state.last_diagnostics_at == 21
    assert {c.name for c in report.charts} == {"location", "scale", "follower", "aci"}
    # A gap change recomputes the follower thresholds and says so.
    state, report = run_monitors(
        state, post_pits=[0.5], follower_pits=[], matured_owner_posts=22, gap_days=1.0
    )
    assert state.follower.gap_days == 1.0 and report.notes and report.diagnostics is None
    # A run of very low PITs alarms the location chart; D2 is then skipped.
    draws, y = _goal_world(rng, n=40)
    goal = GoalInputs(draws=draws, outcomes=y, goal=float(np.quantile(draws, 0.5)))
    state, report = run_monitors(
        state, post_pits=[0.0005] * 6, follower_pits=[], matured_owner_posts=70, gap_days=1.0, goal=goal
    )
    assert any(a.chart == "location" and a.side == "lower" for a in report.alarms)
    assert any(a.chart == "scale" for a in report.alarms)
    assert not report.d1_quiet and report.goal is not None and report.goal.status == "skipped_d1_alarm"
    loc = next(c for c in report.charts if c.name == "location")
    assert loc.alarmed_this_run and loc.alarm_count >= 1
    # 40 in-control posts later D1 is quiet again and D2 runs (a constant PIT of 0.5 would alarm the
    # lower scale chart: z = 0 every time is too little spread).
    state, report = run_monitors(
        state,
        post_pits=list(rng.permutation(np.linspace(0.02, 0.98, 40))),
        follower_pits=[],
        matured_owner_posts=110,
        gap_days=1.0,
        goal=goal,
    )
    assert report.d1_quiet and report.goal is not None and report.goal.status == "goal_ok"
    json.dumps(report.to_dict())


# ---------------------------------------------------------------- ARL0 (M3 acceptance), fast versions


def test_location_arl0_fast() -> None:
    check_arl0(location_chart(), (300.0,), reps=400, tol=0.20, seed=101)


def test_scale_arl0_fast() -> None:
    check_arl0(scale_chart(), (20.0,), reps=400, tol=0.20, seed=102)


def test_follower_arl0_fast() -> None:
    check_arl0(follower_chart(3.0), (30.0,), reps=400, tol=0.20, seed=103)


# ---------------------------------------------------------------- ARL0 (M3 acceptance), ≥ 1,000 replicates


@pytest.mark.slow
def test_location_arl0_matches_300() -> None:
    means = check_arl0(location_chart(), POST_MUS, reps=4000, tol=0.10, seed=201)
    print("location ARL0 by (μ, φ), then pooled:", [round(m, 1) for m in means])


@pytest.mark.slow
def test_scale_arl0_matches_300() -> None:
    means = check_arl0(scale_chart(), POST_MUS, reps=4000, tol=0.10, seed=202)
    print("scale ARL0 by (μ, φ), then pooled:", [round(m, 1) for m in means])


@pytest.mark.slow
@pytest.mark.parametrize("gap", [1.0, 2.0, 3.0])
def test_follower_arl0_matches_100_days(gap: float) -> None:
    chart = follower_chart(gap)
    means = check_arl0(
        chart, tuple(m * gap for m in FOLLOWER_BASE_MUS), reps=4000, tol=0.10, seed=203 + int(gap)
    )
    print(
        f"follower G={gap:g} target {chart.target_arl0:.1f} h {chart.h_upper:.3f}:",
        [round(m, 1) for m in means],
    )


@pytest.mark.slow
def test_aci_in_control_run_length() -> None:
    rl = aci_run_lengths(np.random.default_rng(204), 1000, nb_source(30.0, 3.0))
    print("D3 in-control mean run length:", rl.mean(), "median", np.median(rl))
    assert rl.mean() > 3000  # no stated target; far rarer than the post charts' 300


@pytest.mark.slow
def test_berkowitz_size_slow() -> None:
    rng = np.random.default_rng(205)
    p = np.array([berkowitz_test(rng.normal(size=63)).p_value for _ in range(2000)])
    print("Berkowitz size at 0.05 / 0.01, n = 63:", np.mean(p < 0.05), np.mean(p < 0.01))
    assert 0.03 <= np.mean(p < 0.05) <= 0.08


# ---------------------------------------------------------------- ARL1 for a 30% drop (MODEL §4.4)

# MODEL §4.4 (single chart, h = 5.61): ARL1 29.8 / 17.6 / 9.1, P(alarm ≤ 10) 0.10 / 0.24 / 0.72.
ARL1_MODEL_561 = {1.5: 29.8, 3.0: 17.6, 8.0: 9.1}
# Recorded in socialmgr/modelling/monitors/charts.py for the deployed h = 7.62.
ARL1_DEPLOYED_762 = {1.5: 43.2, 3.0: 23.6, 8.0: 11.9}


def _arl1(h: float, phi: float) -> NDArray[np.int64]:
    chart = location_chart(h=h)
    return cusum_run_lengths(
        np.random.default_rng([4401, int(phi * 10)]), 4000, chart, nb_source(500.0, phi, 0.7)
    )


@pytest.mark.slow
@pytest.mark.parametrize("phi", PHIS)
def test_arl1_for_a_30pct_drop(phi: float) -> None:
    single, deployed = _arl1(5.61, phi), _arl1(LOCATION_H, phi)
    print(
        f"φ={phi}: h=5.61 ARL1 {single.mean():.1f} P≤10 {np.mean(single <= 10):.2f}; "
        f"h=7.62 ARL1 {deployed.mean():.1f} P≤10 {np.mean(deployed <= 10):.2f} "
        f"P≤20 {np.mean(deployed <= 20):.2f}"
    )
    assert single.mean() == pytest.approx(ARL1_MODEL_561[phi], rel=0.05)  # reproduces MODEL's simulation
    assert deployed.mean() == pytest.approx(ARL1_DEPLOYED_762[phi], rel=0.03)
