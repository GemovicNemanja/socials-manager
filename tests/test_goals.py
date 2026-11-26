"""Goal status with hysteresis (MODEL.md §4.2; plan §6: no default goals in v1)."""

import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from socialmgr.modelling.followers import FollowerGoal, forecast_followers
from socialmgr.modelling.goals import (
    EPSILON,
    FOLLOWER_SCOPE,
    POSTS_SCOPE,
    GoalStatus,
    ScopePost,
    classify,
    follower_goal_status,
    no_goal_status,
    posts_goal_status,
    scope_effect,
)
from tests.test_followers import _fit, _sampler

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
NARROW = 0.1  # an 80% interval width well under 4ε
WIDE = 4 * EPSILON + 0.01


# --- the status rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p_hi", "p_lo", "width", "previous", "expected"),
    [
        (0.80, 0.0, NARROW, None, ("working", None)),
        (0.79, 0.0, NARROW, None, ("near_threshold", "close")),
        (0.0, 0.80, NARROW, None, ("not_working", None)),
        (0.5, 0.3, WIDE, None, ("near_threshold", "uncertain")),
        # leaving Working needs p_hi < 0.70 (to Near) or p_lo ≥ 0.85 (to Not working)
        (0.71, 0.0, NARROW, "working", ("working", None)),
        (0.69, 0.0, NARROW, "working", ("near_threshold", "close")),
        (0.69, 0.0, WIDE, "working", ("near_threshold", "uncertain")),
        (0.0, 0.84, NARROW, "working", ("near_threshold", "close")),
        (0.0, 0.85, NARROW, "working", ("not_working", None)),
        # Not working is symmetric
        (0.0, 0.71, NARROW, "not_working", ("not_working", None)),
        (0.0, 0.69, NARROW, "not_working", ("near_threshold", "close")),
        (0.85, 0.0, NARROW, "not_working", ("working", None)),
        (0.84, 0.0, NARROW, "not_working", ("near_threshold", "close")),
        # from Near the 0.80 entry thresholds apply
        (0.79, 0.0, NARROW, "near_threshold", ("near_threshold", "close")),
        (0.80, 0.0, NARROW, "near_threshold", ("working", None)),
        (0.0, 0.80, NARROW, "near_threshold", ("not_working", None)),
    ],
)
def test_classify_thresholds_and_hysteresis(
    p_hi: float, p_lo: float, width: float, previous: str | None, expected: tuple[str, str | None]
) -> None:
    assert classify(p_hi, p_lo, width, previous=previous) == expected  # type: ignore[arg-type]


def test_insufficient_data_is_always_near_threshold() -> None:
    assert classify(0.99, 0.0, NARROW, insufficient=True, previous="working") == (
        "near_threshold",
        "insufficient_owner_data",
    )


# --- posts goals -------------------------------------------------------------------------------------------


def _posts(n: int, residual: float, sd: float = 0.6) -> list[ScopePost]:
    return [ScopePost(f"p{k}", math.expm1(6.0 + residual), 6.0, sd) for k in range(n)]


def test_scope_effect_is_the_conjugate_normal_update() -> None:
    posts = _posts(10, 0.3, sd=0.6)
    eff = scope_effect(posts)
    precision = 1 / 0.09 + 10 / 0.36
    assert eff.sd == pytest.approx(math.sqrt(1 / precision))
    assert eff.mean == pytest.approx((10 * 0.3 / 0.36) / precision)
    assert scope_effect([]).mean == 0.0 and scope_effect([]).sd == pytest.approx(0.3)


def test_scope_post_from_draws() -> None:
    draws = np.random.default_rng(0).lognormal(6.0, 0.5, 4000)
    p = ScopePost.from_draws("x", 403.0, draws)
    assert p.pred_log_median == pytest.approx(6.0, abs=0.03)
    assert p.pred_log_sd == pytest.approx(0.5, abs=0.03)
    assert p.log_residual == pytest.approx(0.0, abs=0.05)


def test_posts_goal_probabilities_integrate_the_scope_effect() -> None:
    base = np.random.default_rng(1).normal(math.log(400), 0.15, 1000)
    posts = _posts(21, 0.0)
    st = posts_goal_status(400.0, base, posts, as_of=T0)
    # Monte Carlo check of the analytic integration over b_S
    eff = scope_effect(posts)
    rng = np.random.default_rng(2)
    theta = np.repeat(base, 200) + eff.mean + eff.sd * rng.standard_normal(base.size * 200)
    assert st.p_hi == pytest.approx(float(np.mean(theta > math.log(400) + EPSILON)), abs=0.005)
    assert st.p_lo == pytest.approx(float(np.mean(theta < math.log(400) - EPSILON)), abs=0.005)
    lo, hi = st.interval80 or (0.0, 0.0)
    assert lo == pytest.approx(float(np.quantile(theta, 0.1)), abs=0.01)
    assert hi == pytest.approx(float(np.quantile(theta, 0.9)), abs=0.01)
    assert st.status == "near_threshold" and st.reason == "uncertain"  # 80% width ≈ 0.49 > 4ε
    centred = np.full(1000, math.log(400.0) + 0.02)
    narrow = posts_goal_status(400.0, centred, _posts(21, 0.0, sd=0.3), as_of=T0)
    assert narrow.status == "near_threshold" and narrow.reason == "close"


def test_posts_goal_moves_with_the_scope_outcomes() -> None:
    base = np.random.default_rng(3).normal(math.log(400), 0.1, 1000)
    up = posts_goal_status(400.0, base, _posts(21, 0.5), as_of=T0)
    down = posts_goal_status(400.0, base, _posts(21, -0.5), as_of=T0)
    assert up.status == "working" and up.p_hi is not None and up.p_hi >= 0.8
    assert down.status == "not_working" and down.p_lo is not None and down.p_lo >= 0.8
    assert up.detail["n_posts"] == 21


def test_posts_goal_uses_last_21_and_needs_five_posts() -> None:
    base = np.full(500, math.log(1000.0))
    few = posts_goal_status(400.0, base, _posts(4, 0.0), as_of=T0)
    assert few.status == "near_threshold" and few.reason == "insufficient_owner_data"
    many = posts_goal_status(400.0, base, _posts(10, -2.0) + _posts(21, 0.0), as_of=T0)
    assert many.detail["n_posts"] == 21 and many.detail["scope_effect_mean"] == pytest.approx(0.0)


# --- history: since, previous_status, goal changes ------------------------------------------------------


def test_status_history_carries_since_and_previous() -> None:
    base_hi = np.full(200, math.log(600.0))  # well above a goal of 400
    base_mid = np.full(200, math.log(400.0) + EPSILON + 0.03)  # p_hi ≈ 0.76 with b_S sd ≈ 0.043
    posts = _posts(21, 0.0, sd=0.2)
    s1 = posts_goal_status(400.0, base_hi, posts, as_of=T0)
    assert s1.status == "working" and s1.since == T0 and s1.previous_status is None
    s2 = posts_goal_status(400.0, base_mid, posts, as_of=T0 + timedelta(days=1), previous=s1)
    assert s2.p_hi is not None and 0.70 <= s2.p_hi < 0.80  # inside the hysteresis band
    assert s2.status == "working" and s2.since == T0  # hysteresis keeps Working
    fresh = posts_goal_status(400.0, base_mid, posts, as_of=T0 + timedelta(days=1))
    assert fresh.status == "near_threshold"  # without history the entry threshold applies
    s3 = posts_goal_status(
        400.0, np.full(200, math.log(300.0)), posts, as_of=T0 + timedelta(days=2), previous=s2
    )
    assert (
        s3.status == "not_working" and s3.since == T0 + timedelta(days=2) and s3.previous_status == "working"
    )
    s4 = posts_goal_status(
        400.0, np.full(200, math.log(290.0)), posts, as_of=T0 + timedelta(days=3), previous=s3
    )
    assert s4.since == s3.since and s4.previous_status == "working"
    # a new goal starts afresh: no hysteresis from the old goal, history still notes the old status
    s5 = posts_goal_status(420.0, base_mid, posts, as_of=T0 + timedelta(days=4), previous=s4)
    assert s5.since == T0 + timedelta(days=4) and s5.previous_status == "not_working"
    assert GoalStatus.from_dict(json.loads(json.dumps(s5.to_dict()))) == s5


def test_no_goal_set() -> None:
    st = no_goal_status(POSTS_SCOPE, T0)
    assert st.status == "no_goal" and st.reason == "no_goal_set" and st.label == "No goal set"
    later = no_goal_status(POSTS_SCOPE, T0 + timedelta(days=1), previous=st)
    assert later.since == T0
    assert GoalStatus.from_dict(st.to_dict()) == st


# --- follower goals ---------------------------------------------------------------------------------------


def test_follower_goal_status_from_forecast() -> None:
    fit = _fit(120)
    sampler = _sampler(fit)
    f0 = fit.followers_last or 0
    easy = FollowerGoal(int(f0 * 0.9), 30)
    fc = forecast_followers(fit, {"original": 3.0}, sampler, goal=easy, scenarios=None, seed=1)
    st = follower_goal_status(fc, easy)
    assert st.scope == FOLLOWER_SCOPE and st.status == "working" and st.horizon_days == 30
    assert st.p_hi == pytest.approx(fc.prob_gt(30, easy.target * 1.1))
    hard = FollowerGoal(int(f0 * 1.6), 30)
    fc_hard = forecast_followers(fit, {"original": 3.0}, sampler, goal=hard, scenarios=None, seed=1)
    st_hard = follower_goal_status(fc_hard, hard, previous=st)
    assert st_hard.status == "not_working" and st_hard.since == fc_hard.as_of
    assert st_hard.p_hi is not None and st_hard.p_lo is not None and st_hard.p_hi + st_hard.p_lo <= 1.0
    assert GoalStatus.from_dict(json.loads(json.dumps(st_hard.to_dict()))) == st_hard


def test_prior_only_follower_fit_reads_insufficient() -> None:
    fit = _fit(20)
    goal = FollowerGoal(int((fit.followers_last or 0) * 0.9), 30)
    fc = forecast_followers(fit, {"original": 3.0}, _sampler(fit), goal=goal, scenarios=None, seed=1)
    st = follower_goal_status(fc, goal)
    assert st.status == "near_threshold" and st.reason == "insufficient_owner_data"
    assert follower_goal_status(fc, goal, insufficient=False).status == "working"
