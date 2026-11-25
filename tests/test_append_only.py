import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.db.models import APPEND_ONLY_TABLES

UPDATES = {
    "metric_snapshots": "UPDATE metric_snapshots SET impressions = 0",
    "forecasts": "UPDATE forecasts SET kind = 'live'",
    "forecast_draws": "UPDATE forecast_draws SET n = 0",
    "scores": "UPDATE scores SET pit_mid = 0.5",
}


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_update_is_rejected(loaded: sessionmaker[Session], table: str) -> None:
    with loaded() as session, pytest.raises(IntegrityError, match="append-only"):
        session.execute(text(UPDATES[table]))


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_delete_is_rejected(loaded: sessionmaker[Session], table: str) -> None:
    with loaded() as session, pytest.raises(IntegrityError, match="append-only"):
        session.execute(text(f"DELETE FROM {table}"))


def test_inserts_still_work(loaded: sessionmaker[Session]) -> None:
    with loaded() as session:
        before = session.scalar(text("SELECT count(*) FROM forecast_draws"))
        session.execute(
            text(
                "INSERT INTO forecast_draws (hash, n, dtype, payload, workspace_id) "
                "VALUES ('x', 0, '<f8', x'', 1)"
            )
        )
        assert session.scalar(text("SELECT count(*) FROM forecast_draws")) == (before or 0) + 1
