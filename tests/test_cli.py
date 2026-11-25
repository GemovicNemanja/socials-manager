"""CLI guards: the fixture never touches the real X account or a real database."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from socialmgr.cli import app


def test_fixture_forces_fake_providers_and_refuses_real_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOCIALMGR_PLUGINS__PROVIDERS", '["x"]')  # as in a member's real .env
    runner = CliRunner()
    ok = runner.invoke(app, ["fixture", "--until", "2026-10-20T06:00:00+00:00"])
    assert ok.exit_code == 0, ok.output
    assert "Fixture loaded: 7 runs" in ok.output  # Oct 14-20

    import sqlite3

    with sqlite3.connect(tmp_path / "data" / "socialmgr.sqlite3") as db:
        assert {r[0] for r in db.execute("select provider from metric_snapshots")} == {"fake_owner"}
        db.execute("update runs set trigger = 'cli' where id = 1")
    refused = runner.invoke(app, ["fixture", "--until", "2026-10-21T06:00:00+00:00"])
    assert refused.exit_code == 1 and "refusing" in refused.output
