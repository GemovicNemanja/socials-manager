"""Champion selection (MODEL §5): the latest promotion review names the champion for forecasts
made after it; forecasts made before it still recompute with the model that made them."""

import dataclasses
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from socialmgr.core.config import PluginConfig, RunConfig
from socialmgr.db import migrate
from socialmgr.db.models import Forecast, ModelReview
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling.llm import NoLabeler
from socialmgr.services import pipeline, runs
from socialmgr.services.fixtures import load_fixture
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings
from tests.test_review_fixes import T


def test_a_promotion_switches_new_forecasts_and_old_ones_still_recompute(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    plugins = dataclasses.replace(load_plugins(settings), labeler=NoLabeler())
    with session_factory(engine)() as session:
        load_fixture(session, settings, plugins, until=T)
        before = {fc.id for fc in session.scalars(select(Forecast))}
        last = pipeline.last_finished_run(session, 1)
        assert last is not None
        session.add(
            ModelReview(
                workspace_id=1,
                run_id=last.id,
                owner_n=99,
                candidate_plugin="trailing_median",
                candidate_version=None,
                champion_before="nb2",
                champion_after="trailing_median",
                decision="promoted",
                reason="test",
                gates=[],
                backtest={},
                created_at=last.cutoff_at,
            )
        )
        session.commit()
        runs.execute(
            session, settings, plugins, clock=lambda: T + timedelta(days=1), cutoff=T + timedelta(days=1)
        )
        new = [fc for fc in session.scalars(select(Forecast)) if fc.id not in before]
        assert new and all(fc.input_manifest["model_plugin"] == "trailing_median" for fc in new)
        assert all(fc.model_version.startswith("trailing_median@") for fc in new)
        for fc in session.scalars(select(Forecast).where(Forecast.id.in_(before))):
            version, _, _ = pipeline.forecast_from_manifest(plugins, fc.input_manifest)
            assert version == fc.model_version and version.startswith("nb2@")
