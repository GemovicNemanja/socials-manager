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
