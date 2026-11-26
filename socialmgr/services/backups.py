"""Database backups (IMPLEMENTATION_PLAN.md §3). Frozen forecasts and assignments can't be recreated.

Every backup is a full copy of the database. The journal's markdown (weekly entries, experiment
notes), which lives outside the database, is mirrored next to the backups each time.
"""

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from socialmgr.core.config import Settings


def backup(settings: Settings, now: datetime | None = None) -> Path:
    """Consistent online copy via SQLite's backup API (safe while the app is running)."""
    target_dir = settings.resolved_backup_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    when = (now or datetime.now(UTC)).replace(microsecond=0)
    target = target_dir / f"socialmgr-{when:%Y%m%dT%H%M%SZ}.sqlite3"
    while target.exists():  # never overwrite a backup made in the same second
        when += timedelta(seconds=1)
        target = target_dir / f"socialmgr-{when:%Y%m%dT%H%M%SZ}.sqlite3"
    with sqlite3.connect(settings.db_path) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    _check(target)
    _mirror_journal(settings, target_dir)
    return target


def _mirror_journal(settings: Settings, target_dir: Path) -> None:
    """Copy entries and experiment notes into ``<backups>/journal`` (adds and updates, never deletes)."""
    for part in ("entries", "experiments"):
        src = settings.journal_dir / part
        if src.is_dir():
            shutil.copytree(src, target_dir / "journal" / part, dirs_exist_ok=True)


def prune(settings: Settings) -> list[Path]:
    """Keep the newest backup of each of the last ``backup_keep_daily`` days, of each of the last
    ``backup_keep_weekly`` ISO weeks before that, and of every month before that (unless
    ``backup_keep_monthly_forever`` is off); delete the rest. Returns the deleted files."""
    files = sorted(settings.resolved_backup_dir.glob("socialmgr-*.sqlite3"), reverse=True)
    keep: set[Path] = set()
    days: list[str] = []
    weeks: list[str] = []
    months: set[str] = set()  # months already holding a kept backup
    for f in files:  # newest first
        try:
            stamp = datetime.strptime(f.stem.removeprefix("socialmgr-"), "%Y%m%dT%H%M%SZ")
        except ValueError:
            keep.add(f)  # not ours: leave it alone
            continue
        day = stamp.strftime("%Y-%m-%d")
        iso = stamp.isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        month = stamp.strftime("%Y-%m")
        if day in days or week in weeks:
            continue
        if len(days) < settings.run.backup_keep_daily:
            days.append(day)
        elif len(weeks) < settings.run.backup_keep_weekly:
            weeks.append(week)
        elif not settings.run.backup_keep_monthly_forever or month in months:
            continue
        months.add(month)
        keep.add(f)
    deleted = [f for f in files if f not in keep]
    for f in deleted:
        f.unlink()
    return deleted


def restore(settings: Settings, source: Path) -> None:
    """Replace the live database with ``source`` after an integrity check. The current database is
    backed up first, so a restore can always be undone."""
    _check(source)
    if settings.db_path.exists():
        backup(settings)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(settings.db_path) as dst:
        src.backup(dst)
    _check(settings.db_path)


def _check(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"integrity check failed for {path}: {result}")
