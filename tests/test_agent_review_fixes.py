"""Regression tests for what an agent reading a brand-new account through MCP found (2026-11-26)."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import PluginConfig, RunConfig, Settings
from socialmgr.db import migrate
from socialmgr.db.models import Run
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.services import agent, agent_m3, experiments, follower_views, overview, runs
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings

T = datetime(2026, 11, 25, 16, 0, tzinfo=UTC)


def _new_account(tmp_path: Path) -> tuple[Session, Settings]:
    """Two runs two hours apart on an account first seen today: the owner's situation on day one."""
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    session = session_factory(engine)()
    plugins = dataclasses.replace(load_plugins(settings), labeler=NoLabeler())
    for at in (T, T + timedelta(hours=2)):
        runs.execute(session, settings, plugins, clock=lambda at=at: at, cutoff=at)
    return session, settings


def test_rates_need_a_day_of_history_and_replies_are_per_7_days(tmp_path: Path) -> None:
    """'Replies per day 11.6' was 1 reply over 2 hours of follower history."""
    session, settings = _new_account(tmp_path)
    with session:
        tiles = follower_views.followers_view(session, settings).tiles
        assert tiles.new_follows_7d.per_day is None and tiles.profile_visits_7d.per_day is None
        if tiles.replies_7d.value is not None:
            assert tiles.replies_7d.per_day == tiles.replies_7d.value / 7


def test_finished_runs_show_steps_they_never_ran_as_not_run(tmp_path: Path) -> None:
    """A finished run listed steps added after it (or skipped) as 'pending'."""
    session, settings = _new_account(tmp_path)
    with session:
        run = session.scalars(select(Run).order_by(Run.id.desc())).first()
        assert run is not None
        run.steps = {k: v for k, v in run.steps.items() if k != "followers"}
        session.commit()
        status = agent.get_run_status(session, settings)
        assert status.latest is not None
        by_name = {s.name: s.status for s in status.latest.steps}
        assert by_name["followers"] == "not_run" and "pending" not in by_name.values()


def test_scorecard_names_how_many_posts_the_median_used(
    loaded: sessionmaker[Session], settings: Settings
) -> None:
    """The label said 'last 21 matured posts' whatever the count."""
    with loaded() as session:
        ov = overview.overview(session, settings)
        lines = overview.account_scorecard(ov)
        n = ov.tiles.median_i48.n_posts
        assert any(f"last {n} matured post" in line for line in lines)
        assert not any("arrive in M3" in line for line in lines) and ov.pending_areas == []


def test_suggestion_detectability_is_labelled_as_the_reference_figure(
    loaded: sessionmaker[Session], settings: Settings
) -> None:
    """MODEL's 92-98% for E03 read as a claim about this account, next to a live preview of 48%."""
    with loaded() as session:
        listed = experiments.get_experiments(session, settings)
        e03 = next(s for s in listed.suggestions if s.key == "E03")
        assert e03.detectability.startswith("MODEL §6.10 reference, assuming 3 originals a day for 4 weeks")
        assert "your own posting rate" in e03.detectability
        assert all(
            "assuming " in s.detectability and "assuming :" not in s.detectability for s in listed.suggestions
        )


def test_model_status_separates_originals_from_all_matured_posts(
    loaded: sessionmaker[Session], settings: Settings
) -> None:
    """model_status said 0 matured posts while other tools said 1 (originals vs all posts)."""
    with loaded() as session:
        s = agent_m3.model_status(session, settings).summary
        assert "matured originals and quotes" in s and "matured posts in all, replies included" in s


def test_the_prior_only_caveat_is_said_once_and_names_the_40_post_review(
    loaded: sessionmaker[Session], settings: Settings
) -> None:
    """get_account_overview showed 'prior-only until 20 matured posts' next to the 40-post review rule."""
    with loaded() as session:
        caveats = agent.get_account_overview(session, settings).caveats
        prior = [c for c in caveats if "priors only" in c or "prior-only" in c]
        assert len(prior) == 1 and "first reviewed at 40 matured posts" in prior[0]
