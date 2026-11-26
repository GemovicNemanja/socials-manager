import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import RunConfig, Settings
from socialmgr.db.models import Forecast, Post
from socialmgr.services.backups import backup, prune, restore
from tests.conftest import make_settings


def test_round_trip(settings: Settings, loaded: sessionmaker[Session]) -> None:
    with loaded() as session:
        posts = session.scalar(select(func.count(Post.id)))
        forecasts = session.scalar(select(func.count(Forecast.id)))

    path = backup(settings)
    assert path.exists() and path.parent == settings.resolved_backup_dir

    with loaded() as session:  # a mutable change after the backup
        session.execute(text("UPDATE posts SET text = NULL"))
        session.commit()

    restore(settings, path)

    with loaded() as session:
        assert session.scalar(select(func.count(Post.id))) == posts
        assert session.scalar(select(func.count(Forecast.id))) == forecasts
        assert session.scalar(select(func.count(Post.id)).where(Post.text.is_(None))) == 0


def test_restore_backs_up_the_current_database_first(
    settings: Settings, loaded: sessionmaker[Session]
) -> None:
    path = backup(settings, datetime(2026, 11, 1, tzinfo=UTC))
    before = set(settings.resolved_backup_dir.glob("socialmgr-*.sqlite3"))
    restore(settings, path)
    after = set(settings.resolved_backup_dir.glob("socialmgr-*.sqlite3"))
    assert len(after - before) == 1  # the database as it was just before the restore


def test_backups_mirror_the_journal_markdown_and_never_drop_it(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    sqlite3.connect(settings.db_path).close()
    entries = settings.journal_dir / "entries"
    entries.mkdir(parents=True)
    (entries / "week-00.md").write_text("week 0")
    (settings.journal_dir / "experiments").mkdir()
    (settings.journal_dir / "experiments" / "E03.md").write_text("notes")
    backup(settings)
    mirror = settings.resolved_backup_dir / "journal"
    assert (mirror / "entries" / "week-00.md").read_text() == "week 0"
    assert (mirror / "experiments" / "E03.md").read_text() == "notes"
    (entries / "week-00.md").write_text("week 0, edited")
    (settings.journal_dir / "experiments" / "E03.md").unlink()
    backup(settings)
    assert (mirror / "entries" / "week-00.md").read_text() == "week 0, edited"
    assert (mirror / "experiments" / "E03.md").exists()  # a deleted note stays in the backups


def test_prune_keeps_one_backup_per_month_forever(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, run=RunConfig(backup_keep_daily=3, backup_keep_weekly=2))
    folder = settings.resolved_backup_dir
    folder.mkdir(parents=True)
    start = datetime(2026, 1, 1, 6, tzinfo=UTC)
    for day in range(300):  # one backup a day, plus a second one on some days
        for hour in (6, 18) if day % 7 == 0 else (6,):
            stamp = (start + timedelta(days=day)).replace(hour=hour)
            (folder / f"socialmgr-{stamp:%Y%m%dT%H%M%SZ}.sqlite3").touch()
    (folder / "my-own-copy.sqlite3").touch()
    prune(settings)
    kept = sorted(f.name for f in folder.glob("*.sqlite3"))
    stamps = [datetime.strptime(n[10:25], "%Y%m%dT%H%M%S") for n in kept if n.startswith("socialmgr-")]
    assert "my-own-copy.sqlite3" in kept
    newest = start + timedelta(days=299)
    assert len([s for s in stamps if (newest.replace(tzinfo=None) - s).days < 3]) == 3  # daily tier
    months = {s.strftime("%Y-%m") for s in stamps}
    assert months == {f"2026-{m:02d}" for m in range(1, 11)}  # every month since the first backup
    assert len(stamps) == 3 + 2 + len(months) - 1  # October already holds the daily/weekly ones
    assert not set(f.name for f in folder.glob("*.sqlite3")) - set(kept)
    prune(settings)
    assert sorted(f.name for f in folder.glob("*.sqlite3")) == kept  # stable


def test_prune_without_the_monthly_tier(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, run=RunConfig(backup_keep_daily=2, backup_keep_weekly=0, backup_keep_monthly_forever=False)
    )
    folder = settings.resolved_backup_dir
    folder.mkdir(parents=True)
    for m in range(1, 5):
        (folder / f"socialmgr-2026{m:02d}01T060000Z.sqlite3").touch()
    prune(settings)
    assert len(list(folder.glob("*.sqlite3"))) == 2
