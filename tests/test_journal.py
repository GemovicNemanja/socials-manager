"""Public journal (M4): entries, drafts, the snapshot, the static build and publishing.

Publishing runs against a local bare git repository standing in for the public GitHub repo.
"""

import json
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import JournalConfig, PluginConfig, RunConfig, Settings
from socialmgr.journal import drafts, site, snapshot
from socialmgr.journal import entries as je
from socialmgr.services import runs
from socialmgr.services.plugins import load_plugins

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


@pytest.fixture
def web_build(tmp_path: Path) -> Path:
    """A stand-in for web/dist-journal (the real one is built by `pnpm build`)."""
    out = tmp_path / "dist-journal"
    (out / "assets").mkdir(parents=True)
    (out / "journal.html").write_text(
        "<!doctype html><html><head><title>x</title></head><body><div id=root></div></body></html>"
    )
    (out / "assets" / "app.js").write_text("console.log('journal')")
    return out


def _entry(
    settings: Settings, week: int, status: str = "draft", body: str = "Hello world.\n\nMore."
) -> je.Entry:
    e = je.Entry(
        path=je.path_for(settings.journal_dir, week),
        title=f"Week {week} title",
        week=week,
        period_start=date(2026, 10, 14) + timedelta(days=7 * week),
        period_end=date(2026, 10, 20) + timedelta(days=7 * week),
        status=status,
        published_at=NOW if status == "published" else None,
        experiments=["E03"] if week else [],
        body=body,
    )
    je.save(e)
    return e


def test_entries_round_trip_and_publish_refuses_todos(settings: Settings) -> None:
    e = _entry(settings, 1, body="TODO: say why.\n\nNumbers.")
    loaded = je.load_all(settings.journal_dir)
    assert [x.week for x in loaded] == [1] and loaded[0].experiments == ["E03"] and not loaded[0].published
    with pytest.raises(je.EntryError, match="TODO"):
        je.publish(settings.journal_dir, 1, NOW)
    e.body = "Why: to learn.\n\nNumbers."
    je.save(e)
    published = je.publish(settings.journal_dir, 1, NOW)
    assert published.published and published.published_at == NOW and published.summary == "Why: to learn."
    with pytest.raises(je.EntryError, match="no entry"):
        je.publish(settings.journal_dir, 5, NOW)


def test_week0_draft_introduces_the_plan(loaded: sessionmaker[Session], settings: Settings) -> None:
    with loaded() as session:
        entry = drafts.draft_week(session, settings, 0, now=NOW)
        assert entry.week == 0 and entry.status == "draft" and "forecast" in entry.body.lower()
        assert "TODO" in entry.body  # the owner's own words go there; publishing refuses until then
        later = drafts.draft_week(session, settings, 3, now=NOW)
        assert later.title.startswith("TODO") and "matured posts landed inside their 80% range" in later.body
        with pytest.raises(je.EntryError, match="isn't between"):
            drafts.draft_week(session, settings, 99, now=NOW)


def test_snapshot_never_contains_drafts_unless_previewing(
    loaded: sessionmaker[Session], settings: Settings
) -> None:
    _entry(settings, 0, status="published", body="Public words.")
    _entry(settings, 1, status="draft", body="Secret draft words.")
    with loaded() as session:
        public = snapshot.build(session, settings, preview=False, now=NOW)
        preview = snapshot.build(session, settings, preview=True, now=NOW)
    assert [e.slug for e in public.entries] == ["week-0"] and not public.preview
    assert "Secret draft words" not in public.model_dump_json()
    assert [(e.slug, e.draft) for e in preview.entries] == [("week-1", True), ("week-0", False)]
    assert public.stats.followers_now and public.followers and public.stats.posts_scored > 60
    assert [s.number for s in public.method.sections] == [1, 2, 3, 4, 5]
    assert any(s.coverage for s in public.method.sections)


def test_build_writes_the_site_with_published_data_only(
    loaded: sessionmaker[Session], settings: Settings, web_build: Path, tmp_path: Path
) -> None:
    _entry(settings, 0, status="published", body="Public words.")
    _entry(settings, 1, status="draft", body="Secret draft words.")
    out = tmp_path / "site"
    with loaded() as session:
        result = site.build(session, settings, out, web_build=web_build, now=NOW)
    assert result.entries == 1 and result.drafts_left_out == 1
    assert (
        (out / "index.html").exists()
        and (out / "assets" / "app.js").exists()
        and (out / ".nojekyll").exists()
    )
    assert not (out / "journal.html").exists()
    index = (out / "index.html").read_text()
    assert "og:title" in index and "growing in public" in index
    data = json.loads((out / "data.json").read_text())
    assert data["preview"] is False and [e["slug"] for e in data["entries"]] == ["week-0"]
    assert "Secret draft words" not in (out / "data.json").read_text()


def _bare_repo(tmp_path: Path) -> Path:
    remote = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    return remote


def _with_journal(settings: Settings, remote: Path, **kw: Any) -> Settings:
    return settings.model_copy(
        update={
            "journal": JournalConfig(
                repo=str(remote),
                site_url="https://example.github.io/growing/",
                author_name="Test Owner",
                author_email="owner@example.com",
                **kw,
            )
        }
    )


def test_publish_pushes_once_and_is_a_no_op_without_changes(
    loaded: sessionmaker[Session],
    settings: Settings,
    web_build: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site, "WEB_JOURNAL", web_build)
    remote = _bare_repo(tmp_path)
    s = _with_journal(settings, remote)
    _entry(s, 0, status="published", body="Public words.")
    with loaded() as session:
        first = site.publish(session, s, NOW)
        again = site.publish(session, s, NOW)
    assert first.changed and first.commit and not again.changed
    log = (
        subprocess.run(
            ["git", "--git-dir", str(remote), "log", "--format=%an <%ae>|%s", "main"],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()
    )
    assert log == ["Test Owner <owner@example.com>|Update journal (2026-11-26 09:00 UTC)"]
    files = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert {"index.html", "data.json", ".nojekyll", "assets/app.js"} <= set(files)


def test_publish_without_a_repo_says_how_to_configure_it(
    loaded: sessionmaker[Session], settings: Settings
) -> None:
    with loaded() as session, pytest.raises(site.PublishError, match="SOCIALMGR_JOURNAL__REPO"):
        site.publish(session, settings, NOW)


def test_runs_refresh_the_site_only_after_the_first_published_entry(
    tmp_path: Path, fixture_db: Path, web_build: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import make_settings

    monkeypatch.setattr(site, "WEB_JOURNAL", web_build)
    remote = _bare_repo(tmp_path)
    base = make_settings(
        tmp_path / "ws", plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    base.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture_db, base.db_path)
    s = _with_journal(base, remote)
    from socialmgr.db.session import make_engine, session_factory

    plugins = load_plugins(s)
    with session_factory(make_engine(s))() as session:
        last = runs.pipeline.last_finished_run(session, 1)
        assert last is not None
        skip = frozenset({"review_model", "followers"})
        at = last.cutoff_at + timedelta(days=1)
        out = runs.execute(session, s, plugins, clock=lambda: at, cutoff=at, skip=skip)
        assert "nothing published yet" in out.steps["journal"]["result"]["skipped"]
        _entry(s, 0, status="published", body="Public words.")
        at2 = at + timedelta(days=1)
        out2 = runs.execute(session, s, plugins, clock=lambda: at2, cutoff=at2, skip=skip)
        assert out2.steps["journal"]["result"]["published"] is True
    # A broken remote is a caveat in the next briefing, never a failed run.
    broken = _with_journal(base, tmp_path / "missing.git")
    with session_factory(make_engine(broken))() as session:
        at3 = at2 + timedelta(days=1)
        out3 = runs.execute(session, broken, plugins, clock=lambda: at3, cutoff=at3, skip=skip)
        assert out3.steps["journal"]["result"]["published"] is False
        assert "Journal refresh failed" in out3.steps["journal"]["result"]["caveats"][0]
