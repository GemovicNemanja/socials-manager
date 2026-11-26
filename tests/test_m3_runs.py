"""M3 run steps on the full fixture: model review, follower forecasts and scores, goal status."""

import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.db.models import Briefing, FollowerForecast, FollowerScore, GoalStatus, ModelReview
from socialmgr.db.session import make_engine, session_factory
from socialmgr.services import follower_step, follower_views, model_views, pipeline, runs
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings


@pytest.fixture
def full(tmp_path: Path, fixture_db: Path) -> tuple[Session, Settings, Any]:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_db, settings.db_path)
    return session_factory(make_engine(settings))(), settings, load_plugins(settings)


def test_the_fixture_has_a_review_with_every_gate(full: tuple[Session, Settings, Any]) -> None:
    session, settings, _ = full
    review = session.scalars(select(ModelReview)).one()
    assert review.owner_n >= 40 and review.decision in ("promoted", "held")
    keys = {g["key"] for g in review.gates}
    assert {"crps_skill", "coverage", "pit_calibration", "cusum", "width_80", "sampler", "brier"} <= keys
    if review.decision == "held":
        assert review.champion_after == review.champion_before == "nb2"
    view = model_views.model_health(session, settings)
    assert view.last_review is not None and view.skill.n == review.backtest["n"]
    assert view.card.follower_elasticity is not None


def test_follower_forecasts_are_frozen_scored_and_shown(full: tuple[Session, Settings, Any]) -> None:
    session, settings, _ = full
    assert session.scalar(select(func.count(FollowerForecast.id))) >= 3 * 9
    scores = session.scalars(select(FollowerScore)).all()
    assert scores and all(s.outcome in ("above", "as_expected", "below") for s in scores)
    view = follower_views.followers_view(session, settings)
    assert view.forecast is not None and set(view.forecast.horizons) == {"7", "30", "90"}
    assert len(view.forecast.band) == 91 and view.forecast.band[0].q50 == view.forecast.start_followers
    assert view.past and view.past[0].outcome in ("above", "as_expected", "below")
    assert view.tiles.followers.expected is not None  # the 7-day forecast made a week ago, scored
    assert {s.label for s in view.scenarios} >= {"3 originals a day"}
    briefing = session.scalars(select(Briefing).order_by(Briefing.run_id.desc())).first()
    assert briefing is not None and "Followers: the 7-day forecast" in briefing.summary


def test_goals_get_a_status_with_history_at_the_next_run(full: tuple[Session, Settings, Any]) -> None:
    session, settings, plugins = full
    last = pipeline.last_finished_run(session, 1)
    assert last is not None
    now = last.cutoff_at + timedelta(hours=1)
    target_at = now + timedelta(days=30)
    preview = follower_step.preview(
        session, settings, plugins, h.GoalIn(scope="followers", target=700, target_at=target_at), now
    )
    assert preview.paths == 4000 and preview.p_goal is not None and preview.median is not None
    assert follower_views.set_goal(
        session, settings, h.GoalIn(scope="followers", target=700, target_at=target_at), "web", now
    ).saved
    assert follower_views.set_goal(session, settings, h.GoalIn(scope="posts", target=600), "web", now).saved
    for d in (1, 2):
        at = last.cutoff_at + timedelta(days=d)
        runs.execute(
            session, settings, plugins, clock=lambda at=at: at, cutoff=at, skip=frozenset({"review_model"})
        )
    statuses = session.scalars(select(GoalStatus).order_by(GoalStatus.id)).all()
    assert len(statuses) == 4
    assert {s.status for s in statuses} <= {"working", "near", "not_working"}
    second_followers = [s for s in statuses if s.detail.get("horizon_days")][-1]
    assert second_followers.since <= last.cutoff_at + timedelta(days=2)
    goals = follower_views.goals_overview(session, settings)
    assert {g.goal.scope for g in goals.current} == {"followers", "posts"}
    fol = next(g for g in goals.current if g.goal.scope == "followers")
    assert fol.p_goal is not None and fol.median is not None and fol.status != "no_goal"
    briefing = session.scalars(select(Briefing).order_by(Briefing.run_id.desc())).first()
    assert briefing is not None and "Goal (followers):" in briefing.summary
