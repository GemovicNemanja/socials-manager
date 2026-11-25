"""IMPLEMENTATION_PLAN.md §13.3: a second provider, prior source and model plug in through config alone."""

from pathlib import Path

from sqlalchemy import func, select

from socialmgr.core.config import PeerConfig, PluginConfig
from socialmgr.db import migrate
from socialmgr.db.models import Account, Forecast, MetricSnapshot, ModelVersion, Post
from socialmgr.db.session import make_engine, session_factory
from socialmgr.services.fixtures import load_fixture
from socialmgr.services.plugins import load_plugins
from tests.conftest import FIXTURE_UNTIL, make_settings


def test_second_provider_prior_and_model(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        peers=(PeerConfig(handle="@peer_one"),),
        plugins=PluginConfig(
            providers=("fake_owner", "fake_public"),
            prior_source="tests.plugins:flat_prior",
            champion_model="tests.plugins:constant_model",
        ),
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    with session_factory(engine)() as session:
        load_fixture(session, settings, load_plugins(settings), until=FIXTURE_UNTIL)
        session.commit()

        peer = session.scalar(select(Account).where(Account.role == "peer"))
        assert peer is not None and peer.handle == "@peer_one"
        peer_sources = set(
            session.scalars(
                select(MetricSnapshot.impressions_source).join(Post).where(Post.account_id == peer.id)
            )
        )
        assert peer_sources == {"viewCount"}
        assert (
            session.scalar(
                select(func.count(MetricSnapshot.id))
                .where(MetricSnapshot.profile_clicks.is_not(None))
                .join(Post)
                .where(Post.account_id == peer.id)
            )
            == 0
        )

        # Only owner posts get forecasts, all from the configured model and prior.
        versions = set(session.scalars(select(Forecast.model_version)))
        assert versions == {"constant@test+flat"}
        families = set(session.scalars(select(ModelVersion.family)))
        assert families == {"constant"}
        forecast_posts = {int(s.split(":")[1]) for s in session.scalars(select(Forecast.subject))}
        owner_posts = set(session.scalars(select(Post.id).join(Account).where(Account.role == "owner")))
        assert forecast_posts == owner_posts
