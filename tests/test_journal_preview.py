"""The public site's local preview (/journal/) and the journal demo fixture its end-to-end tests use."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from socialmgr.api.app import create_app
from socialmgr.core.config import Settings
from socialmgr.journal import entries as je
from socialmgr.journal import snapshot
from socialmgr.services.fixtures import write_journal_demo
from tests.conftest import FIXTURE_UNTIL


def _journal_build(tmp_path: Path) -> Path:
    dist = tmp_path / "dist-journal"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>journal</title><div id=root></div>")
    (dist / "assets" / "journal.js").write_text("console.log('journal')")
    return dist


def test_journal_preview_serves_the_public_build(settings: Settings, tmp_path: Path) -> None:
    client = TestClient(create_app(settings, web_dist=None, journal_dist=_journal_build(tmp_path)))
    page = client.get("/journal/")
    assert page.status_code == 200 and "<title>journal</title>" in page.text
    assert client.get("/journal/assets/journal.js").text == "console.log('journal')"
    # Relative URLs (./assets, ./data.json) need the trailing slash.
    bare = client.get("/journal", follow_redirects=False)
    assert bare.status_code in (302, 307) and bare.headers["location"] == "/journal/"
    # No data.json in the build: the page falls back to the preview API, which must still answer.
    assert client.get("/journal/data.json").status_code == 404


def test_journal_preview_absent_without_a_build(settings: Settings, tmp_path: Path) -> None:
    client = TestClient(create_app(settings, web_dist=None, journal_dist=tmp_path / "missing"))
    assert client.get("/journal/").status_code == 404


def test_journal_preview_sits_before_the_dashboard_catch_all(settings: Settings, tmp_path: Path) -> None:
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<title>dashboard</title>")
    client = TestClient(create_app(settings, web_dist=web, journal_dist=_journal_build(tmp_path)))
    assert "<title>journal</title>" in client.get("/journal/").text
    assert "<title>dashboard</title>" in client.get("/posts").text


def test_journal_demo_writes_a_published_and_a_draft_entry(
    settings: Settings, loaded: sessionmaker[Session]
) -> None:
    with loaded() as session:
        written = write_journal_demo(session, settings, FIXTURE_UNTIL)
        assert len(written) == 2  # no experiments in the plain fixture, so no E03 note
        entries = je.load_all(settings.journal_dir)
        assert [e.status for e in entries] == ["draft", "published"]
        draft, published = entries
        assert draft.week == published.week + 1 and draft.published_at is None
        assert published.published and published.period_end < draft.period_start
        public = snapshot.build(session, settings, preview=False, now=FIXTURE_UNTIL)
        preview = snapshot.build(session, settings, preview=True, now=FIXTURE_UNTIL)
    assert [e.slug for e in public.entries] == [published.slug]
    assert [(e.slug, e.draft) for e in preview.entries] == [(draft.slug, True), (published.slug, False)]
    body = public.entries[0].body_markdown
    for feature in ("## ", "- ", "1. ", "**", "*image*", "](https://", "`", "<script>"):
        assert feature in body


def test_fixture_cli_journal_demo_flag(tmp_path: Path) -> None:
    from socialmgr.cli import app

    env = {
        "SOCIALMGR_DATA_DIR": str(tmp_path / "data"),
        "SOCIALMGR_PLUGINS__LABELER": "none",
        "SOCIALMGR_RUN__REGIME_CHECK": "false",
        "SOCIALMGR_RUN__BACKUP": "false",
    }
    result = CliRunner().invoke(
        app, ["fixture", "--until", "2026-10-20T06:00:00+00:00", "--journal-demo"], env=env
    )
    assert result.exit_code == 0, result.output
    assert "journal files written" in result.output
    names = sorted(p.name for p in (tmp_path / "data" / "journal" / "entries").glob("*.md"))
    assert names == ["week-00.md"]  # six days in: week 0 only, as a draft
    assert je.parse(tmp_path / "data" / "journal" / "entries" / "week-00.md").status == "draft"
