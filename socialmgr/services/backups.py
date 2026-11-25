"""Database backups (IMPLEMENTATION_PLAN.md §3). Frozen forecasts and assignments can't be recreated."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from socialmgr.core.config import Settings


def backup(settings: Settings, now: datetime | None = None) -> Path:
    """Consistent online copy via SQLite's backup API (safe while the app is running)."""
    target_dir = settings.resolved_backup_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"socialmgr-{stamp}.sqlite3"
    with sqlite3.connect(settings.db_path) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    _check(target)
    return target


def prune(settings: Settings) -> list[Path]:
    """Keep the newest backup of each of the last ``backup_keep_daily`` days and of each of the last
    ``backup_keep_weekly`` ISO weeks before that; delete the rest. Returns the deleted files."""
    files = sorted(settings.resolved_backup_dir.glob("socialmgr-*.sqlite3"), reverse=True)
    keep: set[Path] = set()
    days: list[str] = []
    weeks: list[str] = []
    for f in files:  # newest first
        try:
            stamp = datetime.strptime(f.stem.removeprefix("socialmgr-"), "%Y%m%dT%H%M%SZ")
        except ValueError:
            keep.add(f)  # not ours: leave it alone
            continue
        day = stamp.strftime("%Y-%m-%d")
        iso = stamp.isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        if day in days or week in weeks:
            continue
        if len(days) < settings.run.backup_keep_daily:
            days.append(day)
            keep.add(f)
        elif len(weeks) < settings.run.backup_keep_weekly:
            weeks.append(week)
            keep.add(f)
    deleted = [f for f in files if f not in keep]
    for f in deleted:
        f.unlink()
    return deleted


def restore(settings: Settings, source: Path) -> None:
    """Replace the live database with ``source`` after an integrity check."""
    _check(source)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(settings.db_path) as dst:
        src.backup(dst)
    _check(settings.db_path)


def _check(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"integrity check failed for {path}: {result}")
