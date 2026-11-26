"""LFO-CV backtest and release gates (MODEL §5, plan §6): gate statistics, the decision, and seeded
simulations in which a non-prior truth passes and a miscalibrated or thin candidate is held."""

import math
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from socialmgr.core.interfaces import ForecastModel
from socialmgr.core.types import ForecastSubject, PriorContext
from socialmgr.modelling import scoring
from socialmgr.modelling.backtest import (
    BacktestPost,
    BacktestResult,
    BacktestRow,
    ForecastScore,
    run_backtest,
    trailing_goal,
)
from socialmgr.modelling.models.nb2 import NB2PriorModel
from socialmgr.modelling.models.nb2_owner import FitDiagnostics, FittedOwnerNB2, NB2OwnerModel
from socialmgr.modelling.priors.hand_set import HandSetPrior
from socialmgr.modelling.release import (
    ReleaseDecision,
    berkowitz_p,
    binomial_band,
    cusum_first_alarm,
    evaluate_release,
    pit_chi2_p,
)
from socialmgr.modelling.sbc import SBC_RESULTS
from tests.owner_sim import T0, Truth, simulate

PREVIOUS = "nb2@prior-0.1+hand_set@0.2"
OWNER = "nb2@owner-0.1+hand_set@0.2"
# Backtests refit at every run, so the simulations use a lighter sampler; gate 6 reads the
# diagnostics of a production-settings fit on all posts, as a run would.
LIGHT = NB2OwnerModel(warmup=300, draws=500)


# ---------------------------------------------------------------- gate statistics


def test_cusum_alarm_timing() -> None:
    assert cusum_first_alarm([0.0] * 500) is None
    # A +1 shift adds 0.75 per post to S+, which first exceeds 7.62 at the 11th post.
    assert cusum_first_alarm([1.0] * 20) == 10
    assert cusum_first_alarm([-1.0] * 20) == 10
    assert cusum_first_alarm([0.2] * 1000) is None  # below the reference value k = 0.25


def test_binomial_band_matches_model_md() -> None:
    lo, hi = binomial_band(60, 0.8)
    assert (round(lo, 2), round(hi, 2)) == (0.70, 0.90)  # MODEL §4.3: n = 60, 80% band [0.70, 0.90]


def test_berkowitz_and_pit_chi2_detect_miscalibration() -> None:
    rng = np.random.default_rng(4)
    z = rng.normal(size=200)
    assert berkowitz_p(z.tolist()) > 0.01
    ar = np.zeros(200)
    for t in range(1, 200):
        ar[t] = 0.7 * ar[t - 1] + rng.normal() * math.sqrt(1 - 0.49)
    assert berkowitz_p(ar.tolist()) < 0.01
    assert berkowitz_p((1.6 * z).tolist()) < 0.01  # too narrow forecasts
    assert pit_chi2_p(rng.uniform(size=200).tolist()) > 0.01
    assert pit_chi2_p(rng.beta(3, 3, size=200).tolist()) < 0.01


# ---------------------------------------------------------------- decisions on synthetic rows


def _score(pit: float, crps: float, width: float, p_goal: float, in50: bool, in80: bool) -> ForecastScore:
    return ForecastScore(pit, pit, crps, in50, in80, width, 100.0, p_goal)


def _rows(n: int, seed: int = 0) -> list[BacktestRow]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        u = float(rng.uniform())
        hit = bool(rng.uniform() < 0.5)
        rows.append(
            BacktestRow(
                post_id=f"p{i}",
                created_at=T0 + timedelta(days=i),
                outcome=100.0,
                goal=100.0,
                hit_goal=hit,
                n_train=20 + i,
                late_maturity=False,
                candidate=_score(
                    u,
                    0.3 + 0.05 * float(rng.uniform()),
                    90.0,
                    0.7 if hit else 0.3,
                    0.25 < u < 0.75,
                    0.1 < u < 0.9,
                ),
                baseline=_score(float(rng.uniform()), 0.5, 100.0, 0.5, True, True),
            )
        )
    return rows


def _diag(rhat: float = 1.002, ess: float = 2000.0) -> FitDiagnostics:
    names = ("level", "a_in", "a_oon", "beta", "log_phi", "lambda", "rho_th")
    return FitDiagnostics(
        rhat=dict.fromkeys(names, rhat),
        ess_bulk=dict.fromkeys(names, ess),
        chains=4,
        draws_per_chain=1500,
        accept_rw=0.25,
        accept_ind=0.5,
        n_posts=80,
        posterior_mean={},
        posterior_sd={},
    )


def _result(rows: list[BacktestRow], diag: FitDiagnostics | None = None) -> BacktestResult:
    return BacktestResult(OWNER, "trailing_median@0.1+hand_set@0.2", tuple(rows), 20, diag or _diag())


def _failed(decision: ReleaseDecision) -> set[str]:
    return {g.name for g in decision.gates if not g.passed}


def test_all_gates_pass_promotes_the_candidate() -> None:
    decision = evaluate_release(_result(_rows(80)), PREVIOUS)
    assert _failed(decision) == set(), decision.gates
    assert decision.promote and decision.chosen_version == OWNER
    assert [g.name for g in decision.gates] == [
        "crps_skill",
        "coverage",
        "pit_calibration",
        "cusum",
        "width_80",
        "sampler",
        "brier",
    ]
    assert decision.to_dict()["gates"][0]["name"] == "crps_skill"


@pytest.mark.parametrize(
    ("gate", "mutate"),
    [
        ("crps_skill", lambda r: replace(r, candidate=replace(r.candidate, crps_log=0.6))),
        ("width_80", lambda r: replace(r, candidate=replace(r.candidate, width_80=120.0))),
        ("brier", lambda r: replace(r, candidate=replace(r.candidate, p_goal=0.5 if r.hit_goal else 0.9))),
        ("coverage", lambda r: replace(r, candidate=replace(r.candidate, in_80=True))),
        (
            "pit_calibration",
            lambda r: replace(
                r, candidate=replace(r.candidate, pit_rand=0.5 + 0.2 * (r.candidate.pit_rand - 0.5))
            ),
        ),
    ],
)
def test_each_failed_gate_keeps_the_previous_version(gate: str, mutate: object) -> None:
    assert callable(mutate)
    decision = evaluate_release(_result([mutate(r) for r in _rows(80)]), PREVIOUS)
    assert gate in _failed(decision)
    assert not decision.promote and decision.chosen_version == PREVIOUS
    assert decision.reason.startswith(f"kept {PREVIOUS}: failed") and gate in decision.reason


def test_cusum_gate_fails_on_a_drift() -> None:
    rows = _rows(80)
    drifted = [
        replace(r, candidate=replace(r.candidate, pit_rand=0.97)) if 40 <= i < 55 else r
        for i, r in enumerate(rows)
    ]
    assert "cusum" in _failed(evaluate_release(_result(drifted), PREVIOUS))


def test_sampler_gate_needs_convergence_and_a_recorded_sbc() -> None:
    assert "sampler" in _failed(evaluate_release(_result(_rows(80), _diag(rhat=1.02)), PREVIOUS))
    assert "sampler" in _failed(evaluate_release(_result(_rows(80), _diag(ess=300)), PREVIOUS))
    assert "sampler" in _failed(evaluate_release(_result(_rows(80)), PREVIOUS, sbc_results={}))
    no_diag = BacktestResult(OWNER, "tm", tuple(_rows(80)), 20, None)
    assert "sampler" in _failed(evaluate_release(no_diag, PREVIOUS))
    assert SBC_RESULTS[OWNER].passed  # recorded by the slow SBC test


def test_too_few_backtest_posts_keeps_the_previous_version() -> None:
    decision = evaluate_release(_result(_rows(12)), PREVIOUS)
    assert not decision.promote and [g.name for g in decision.gates] == ["backtest_size"]
    late = [replace(r, late_maturity=True) if i < 70 else r for i, r in enumerate(_rows(80))]
    assert evaluate_release(_result(late), PREVIOUS).gates[0].name == "backtest_size"


def test_shared_monitor_can_replace_the_inline_cusum() -> None:
    decision = evaluate_release(_result(_rows(80)), PREVIOUS, cusum=lambda z: 3)
    assert "cusum" in _failed(decision) and "p3" in next(
        g.detail for g in decision.gates if g.name == "cusum"
    )


# ---------------------------------------------------------------- the backtest itself


def test_backtest_uses_only_what_was_visible_at_creation() -> None:
    posts = simulate(Truth(), 40, np.random.default_rng(2))
    result = run_backtest(posts, NB2PriorModel(), HandSetPrior(), seed=0)
    by_id = {p.example.post_external_id: p for p in posts}
    assert result.rows and all(r.n_train >= 20 for r in result.rows)
    for row in result.rows:
        visible = sum(1 for p in posts if p.visible_at <= row.created_at)
        assert row.n_train == visible
        assert row.goal == trailing_goal(
            [
                p.example
                for p in posts
                if p.visible_at <= row.created_at and p.example.post_external_id != row.post_id
            ]
        )
        assert by_id[row.post_id].example.outcome_i48 == row.outcome
    # Scored exactly like score_matured: the randomized PIT is seeded by the post id.
    row = result.rows[0]
    post = by_id[row.post_id]
    assert post.pit_seed is not None
    assert result.rows[0].candidate.pit_rand == pytest.approx(
        scoring.pit_randomized(
            NB2PriorModel()
            .fit(
                [p.example for p in posts if p.visible_at <= row.created_at], HandSetPrior().prior(_ctx()), 0
            )
            .predict(_subject(post))
            .samples,
            row.outcome,
            post.pit_seed,
        )
    )


def _ctx() -> PriorContext:
    return PriorContext(platform="x", followers=0, owner_matured_posts=0)


def _subject(post: BacktestPost) -> ForecastSubject:
    ex = post.example
    return ForecastSubject(
        subject_id=f"post:{post.pit_seed}",
        created_at=ex.created_at,
        type=ex.type,
        followers_at_post=ex.followers_at_post,
    )


def test_backtest_is_deterministic() -> None:
    posts = simulate(Truth(), 35, np.random.default_rng(8))
    a = run_backtest(posts, LIGHT, HandSetPrior(), seed=3)
    b = run_backtest(posts, LIGHT, HandSetPrior(), seed=3)
    assert a.rows == b.rows and a.candidate_version == OWNER


# ---------------------------------------------------------------- simulations


def _decide(posts: list[BacktestPost], candidate: ForecastModel, seed: int) -> ReleaseDecision:
    backtest = run_backtest(posts, candidate, HandSetPrior(), seed=seed)
    final = NB2OwnerModel().fit([p.example for p in posts], HandSetPrior().prior(_ctx()), seed)
    diag = final.diagnostics if isinstance(final, FittedOwnerNB2) else None
    return evaluate_release(backtest, PREVIOUS, diagnostics=diag, seed=seed)


def _break(i: int, n: int) -> float:
    return 1.0 if i < n // 2 else 0.35  # the owner's reach drops by 65% halfway through


def _scenario_rates(
    truth: Truth, reps: int, n_posts: int, candidate: ForecastModel
) -> tuple[float, dict[str, int]]:
    promoted, failures = 0, dict[str, int]()
    for rep in range(reps):
        decision = _decide(simulate(truth, n_posts, np.random.default_rng(1000 + rep)), candidate, rep)
        promoted += decision.promote
        for name in _failed(decision):
            failures[name] = failures.get(name, 0) + 1
    return promoted / reps, failures


def test_owner_model_passes_the_gates_on_a_non_prior_truth() -> None:
    """a_oon = log 400 (prior mean log 100), phi = 5, 35% replies, followers x5: promoted."""
    rate, failures = _scenario_rates(Truth(), reps=3, n_posts=90, candidate=LIGHT)
    assert rate >= 2 / 3, failures


def test_a_miscalibrated_owner_model_is_held() -> None:
    """A 65% level drop halfway: the owner model (one level for all history) fails calibration."""
    rate, failures = _scenario_rates(Truth(level=_break), reps=3, n_posts=80, candidate=LIGHT)
    assert rate == 0.0
    assert failures.get("pit_calibration", 0) + failures.get("cusum", 0) >= 3, failures


def test_the_prior_only_model_and_thin_data_are_held() -> None:
    rate, failures = _scenario_rates(Truth(), reps=2, n_posts=80, candidate=NB2PriorModel())
    assert rate == 0.0 and failures.get("crps_skill") == 2, failures
    thin = _decide(simulate(Truth(), 30, np.random.default_rng(5)), LIGHT, 0)
    assert not thin.promote and thin.gates[0].name == "backtest_size" and thin.chosen_version == PREVIOUS


@pytest.mark.slow
def test_gate_operating_characteristics_over_replicates() -> None:
    good, good_fail = _scenario_rates(Truth(), reps=20, n_posts=90, candidate=LIGHT)
    bad, bad_fail = _scenario_rates(Truth(level=_break), reps=20, n_posts=90, candidate=LIGHT)
    print(f"non-prior truth: promoted {good:.2f}, failures {good_fail}")
    print(f"level break: promoted {bad:.2f}, failures {bad_fail}")
    # A calibrated model still trips the CUSUM (ARL0 300) or a p > 0.01 test now and then.
    assert good >= 0.6, good_fail
    assert bad <= 0.05, bad_fail
