"""The run's monitor step (MODEL §4.3) on a real database: arming, persistence and alarms."""

import dataclasses
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from socialmgr.core.config import PluginConfig, RunConfig
from socialmgr.core.types import CollectionBatch, CollectionRequest
from socialmgr.db import migrate
from socialmgr.db.models import Alarm, MonitorState
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.providers.fake.provider import FakeOwnerProvider
from socialmgr.services import alarms, model_views, runs
from socialmgr.services.fixtures import HEAVY, load_fixture
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings
from tests.test_review_fixes import T


class Collapsed(FakeOwnerProvider):
    """Reach drops to 3% for posts after ``T``: a drift the location chart must catch."""

    def _metrics(self, post: Any, i48: float, at: Any) -> Any:
        return super()._metrics(post, i48 * (0.03 if post.created_at > T else 1.0), at)

    def collect(self, request: CollectionRequest) -> CollectionBatch:
        return super().collect(request)


def _setup(tmp_path: Path, provider: Any = None) -> tuple[Any, Any, Any]:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    plugins = dataclasses.replace(load_plugins(settings), labeler=NoLabeler())
    if provider is not None:
        plugins = dataclasses.replace(plugins, providers=(provider,))
    return settings, session_factory(engine)(), plugins


def test_fixture_runs_arm_the_charts_and_persist_history(tmp_path: Path) -> None:
    settings, session, plugins = _setup(tmp_path)
    with session:
        load_fixture(session, settings, plugins, until=T)
        rows = session.scalars(select(MonitorState).where(MonitorState.chart == "d1_location")).all()
        assert rows and rows[-1].armed and rows[-1].threshold == 7.62
        assert not rows[0].armed  # fewer than 20 matured posts at the first runs
        view = model_views.model_health(session, settings)
        loc = next(m for m in view.monitors if m.chart == "d1_location")
        assert loc.state in ("quiet", "alarm") and len(loc.series) == len(rows)
        assert view.hit_80.n > 20 and view.pit.n == view.hit_80.n and sum(view.pit.counts) == view.pit.n


def test_a_reach_collapse_raises_one_location_alarm_and_it_can_be_acknowledged(tmp_path: Path) -> None:
    settings, session, plugins = _setup(tmp_path, Collapsed())
    with session:
        load_fixture(session, settings, plugins, until=T)
        for d in range(1, 15):
            at = T + timedelta(days=d)
            runs.execute(session, settings, plugins, clock=lambda at=at: at, cutoff=at, skip=HEAVY)
        # Posts from before the collapse mature for two more days; after that the drop shows.
        raised = session.scalars(
            select(Alarm)
            .where(Alarm.chart == "d1_location", Alarm.raised_at > T + timedelta(days=3))
            .order_by(Alarm.raised_at)
        ).all()
        assert raised and all(a.direction == "down" for a in raised), [
            (a.raised_at, a.direction) for a in raised
        ]
        from socialmgr.db.models import Briefing

        alarm_run = session.scalar(select(Briefing).where(Briefing.run_id == raised[0].run_id))
        assert alarm_run is not None and "Alarm: location drift on 48h impressions" in alarm_run.summary
        # At most one alarm per chart per run.
        assert len({a.run_id for a in raised}) == len(raised)
        acked = alarms.acknowledge(
            session, 1, raised[0].id, "owner", "posted less this week", T + timedelta(days=15)
        )
        assert acked.acknowledged_at is not None
        assert raised[0].id not in {a.id for a in alarms.open_alarms(session, 1)}
