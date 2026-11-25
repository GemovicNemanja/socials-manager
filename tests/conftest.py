import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.db import migrate
from socialmgr.db.session import make_engine, session_factory
from socialmgr.services.fixtures import load_fixture
from socialmgr.services.plugins import load_plugins

FIXTURE_UNTIL = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def factory(settings: Settings) -> sessionmaker[Session]:
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return session_factory(engine)


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the fixture workspace once; each test gets its own copy."""
    template = make_settings(tmp_path_factory.mktemp("template"))
    engine = make_engine(template)
    migrate.upgrade(engine)
    with session_factory(engine)() as session:
        load_fixture(session, template, load_plugins(template), until=FIXTURE_UNTIL)
        session.commit()
    engine.dispose()
    return template.db_path


@pytest.fixture
def loaded(settings: Settings, fixture_db: Path) -> Iterator[sessionmaker[Session]]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_db, settings.db_path)
    engine = make_engine(settings)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture
def ws(tmp_path: Path, fixture_db: Path) -> Iterator[tuple[Session, Settings, Any]]:
    """Experiment workspace: (session, settings, plugins whose fake provider reacts to arms)."""
    from tests.experiment_sim import World, open_copy, sim_settings

    settings = sim_settings(tmp_path)
    session = open_copy(settings, fixture_db)
    yield session, settings, World(session).plugins(settings)
    session.close()
