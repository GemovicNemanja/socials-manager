"""Team setup: .env editing, the guided setup, the Mac app, the daily-run agent, MCP registration and
the background server. Nothing here touches the real ~/Applications, LaunchAgents or Claude Code."""

import os
import plistlib
import shutil
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from socialmgr.cli import app
from socialmgr.core.config import Settings
from socialmgr.setup import claude_code, envfile, macos, server, wizard
from socialmgr.setup.names import LIVE, Names, check_instance

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("SOCIALMGR_"):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def no_real_claude_or_launchd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would run the real `claude` or `launchctl`: an early version of these tests
    re-registered the developer's own MCP server. Tests that need them pass fakes."""

    def refuse(*args: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"a test tried to run a real command: {args}")

    monkeypatch.setattr(claude_code, "_run", refuse)
    monkeypatch.setattr(macos, "_launchctl", refuse)


# ---------------------------------------------------------------- .env


def test_env_update_keeps_comments_and_round_trips_through_settings(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    backup = "/Users/a b/Library/CloudStorage/ProtonDrive-me@proton.me-folder/socialmgr"
    envfile.update(
        env,
        {
            "SOCIALMGR_ACCOUNT__HANDLE": "@tester",  # active line in the template
            "SOCIALMGR_BACKUP_DIR": backup,  # commented default in the template
            "SOCIALMGR_ACCOUNT__PREMIUM": "false",
            "SOCIALMGR_PLUGINS__PROVIDERS": '["x"]',
            "SOCIALMGR_NEW_KEY": 'quote " and \\ back',  # not in the template: appended
        },
        template=ROOT / ".env.example",
    )
    text = env.read_text()
    assert "# Copy to .env and adjust" in text and "SOCIALMGR_ACCOUNT__HANDLE=@your_account" not in text
    assert text.count("SOCIALMGR_BACKUP_DIR=") == 1 and "# SOCIALMGR_BACKUP_DIR=" not in text
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.account.handle == "@tester" and settings.account.premium is False
    assert str(settings.backup_dir) == backup and settings.plugins.providers == ("x",)
    assert envfile.read(env)["SOCIALMGR_NEW_KEY"] == 'quote " and \\ back'
    envfile.update(env, {"SOCIALMGR_ACCOUNT__HANDLE": "@other"})
    assert envfile.read(env)["SOCIALMGR_ACCOUNT__HANDLE"] == "@other"
    assert envfile.read(env)["SOCIALMGR_BACKUP_DIR"] == backup


def test_validation() -> None:
    assert wizard.normalize_handle(" NemanjaGemovic ") == "@NemanjaGemovic"
    assert wizard.normalize_handle("@a_b") == "@a_b"
    for bad in ("", "@", "has space", "@this_is_way_too_long"):
        with pytest.raises(ValueError):
            wizard.normalize_handle(bad)
    assert wizard.check_tz("Europe/Belgrade") == "Europe/Belgrade"
    with pytest.raises(ValueError):
        wizard.check_tz("Mars/Olympus")
    assert wizard.parse_time("7:05") == macos.DailyRun(7, 5)
    for bad in ("24:00", "9", "09:60"):
        with pytest.raises(ValueError):
            wizard.parse_time(bad)
    assert wizard.check_client_id(" aBc123-_xyz:ci ") == "aBc123-_xyz:ci"
    with pytest.raises(ValueError):
        wizard.check_client_id("short")
    wizard.check_tz(wizard.system_tz())


def test_backup_candidates(tmp_path: Path) -> None:
    (tmp_path / "Library/CloudStorage/ProtonDrive-me@proton.me-folder").mkdir(parents=True)
    (tmp_path / "Library/Mobile Documents/com~apple~CloudDocs").mkdir(parents=True)
    found = wizard.backup_candidates(tmp_path)
    assert [label for label, _ in found] == ["ProtonDrive", "iCloud Drive"]
    assert all(path.name == "socialmgr-backups" for _, path in found)


# ---------------------------------------------------------------- the Mac app and the daily run


def test_app_bundle(tmp_path: Path) -> None:
    repo = tmp_path / "my repo"
    app_dir = macos.write_app(repo, tmp_path / "Applications")
    assert app_dir.name == "Social Manager.app"
    with (app_dir / "Contents/Info.plist").open("rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleExecutable"] == "social-manager" and info["CFBundlePackageType"] == "APPL"
    exe = app_dir / "Contents/MacOS/social-manager"
    assert os.access(exe, os.X_OK)
    script = exe.read_text()
    assert f"REPO='{repo}'" in script and "launch" in script
    assert subprocess.run(["bash", "-n", str(exe)]).returncode == 0
    # Rewriting replaces the old bundle (paths change when the repo moves).
    again = macos.write_app(tmp_path / "moved", tmp_path / "Applications")
    assert str(tmp_path / "moved") in (again / "Contents/MacOS/social-manager").read_text()


def test_app_launcher_alerts_when_the_repo_is_gone(tmp_path: Path) -> None:
    app_dir = macos.write_app(tmp_path / "missing", tmp_path)
    exe = app_dir / "Contents/MacOS/social-manager"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "osascript").write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/alert.txt"\n')
    (fake_bin / "osascript").chmod(0o755)
    script = exe.read_text().replace("/usr/bin/osascript", str(fake_bin / "osascript"))
    exe.write_text(script)
    assert subprocess.run(["bash", str(exe)]).returncode == 1
    assert "missing" in (fake_bin / "alert.txt").read_text()


def test_daily_run_agent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    agents = tmp_path / "LaunchAgents"
    assert macos.scheduled(agents) is None
    path = macos.schedule(repo, macos.DailyRun(7, 30), agents, load=False)
    with path.open("rb") as f:
        plist = plistlib.load(f)
    assert plist["Label"] == LIVE.run_label
    assert plist["ProgramArguments"] == [str(repo / ".venv/bin/socialmgr"), "run"]
    assert plist["WorkingDirectory"] == str(repo)
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert "/usr/bin" in plist["EnvironmentVariables"]["PATH"]
    assert (repo / "data/logs").is_dir()
    assert macos.scheduled(agents) == macos.DailyRun(7, 30)
    macos.schedule(repo, macos.DailyRun(6, 0), agents, load=False)
    assert macos.scheduled(agents) == macos.DailyRun(6, 0)
    assert macos.unschedule(agents, load=False) and macos.scheduled(agents) is None


# ---------------------------------------------------------------- Claude Code


class FakeClaude:
    def __init__(self, registered_repo: Path | None = None, logged_in: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.registered = registered_repo
        self.logged_in = logged_in

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        out, code = "", 0
        if cmd[1:3] == ["auth", "status"]:
            out = '{"loggedIn": %s}' % ("true" if self.logged_in else "false")
        elif cmd[1:3] == ["mcp", "get"]:
            code = 0 if self.registered else 1
            out = f"Args: run --directory {self.registered} socialmgr mcp" if self.registered else ""
        return subprocess.CompletedProcess(cmd, code, out, "")


def test_mcp_registration_is_user_scoped_and_reads_this_install(tmp_path: Path) -> None:
    fake = FakeClaude()
    claude_code.register_mcp("claude", tmp_path, "/bin/uv", run=fake)
    assert fake.calls[0] == ["claude", "mcp", "remove", "--scope", "user", "socialmgr"]
    assert fake.calls[1] == [
        "claude", "mcp", "add", "--scope", "user", "socialmgr", "--",
        "/bin/uv", "run", "--quiet", "--directory", str(tmp_path), "socialmgr", "mcp",
    ]  # fmt: skip
    assert not claude_code.mcp_registered("claude", tmp_path, run=fake)
    assert claude_code.mcp_registered("claude", tmp_path, run=FakeClaude(tmp_path))
    assert not claude_code.mcp_registered("claude", tmp_path, run=FakeClaude(tmp_path / "other"))
    assert claude_code.signed_in("claude", run=FakeClaude())
    assert not claude_code.signed_in("claude", run=FakeClaude(logged_in=False))


# ---------------------------------------------------------------- the guided setup


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(ROOT / ".env.example", repo / ".env.example")
    return repo


def _hidden(tmp_path: Path, repo: Path) -> list[str]:
    return [
        "--repo", str(repo), "--uv", "/bin/uv",
        "--applications", str(tmp_path / "Applications"),
        "--launch-agents", str(tmp_path / "LaunchAgents"), "--no-load-agent",
    ]  # fmt: skip


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    """Pretend to be a Mac with a signed-in Claude Code; never open a browser."""
    fake = FakeClaude()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(claude_code, "find", lambda: "claude")
    monkeypatch.setattr(claude_code, "_run", fake)
    monkeypatch.setattr(wizard.webbrowser, "open", lambda *_a, **_k: pytest.fail("opened a browser"))
    monkeypatch.setattr(wizard, "backup_candidates", lambda home=None, folder="": [])
    return fake


def test_setup_non_interactive_then_rerun_keeps_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)
    runner = CliRunner()
    first = runner.invoke(
        app,
        ["setup", "--yes", "--handle", "tester_1", "--tz", "Europe/Belgrade", "--no-premium",
         "--client-id", "Q2xpZW50SWRFeGFtcGxlOjE6Y2k", "--run-at", "07:30", "--no-connect",
         *_hidden(tmp_path, repo)],
    )  # fmt: skip
    assert first.exit_code == 0, first.output
    env = envfile.read(repo / ".env")
    assert env["SOCIALMGR_ACCOUNT__HANDLE"] == "@tester_1"
    assert env["SOCIALMGR_ACCOUNT__TZ"] == "Europe/Belgrade"
    assert env["SOCIALMGR_ACCOUNT__PREMIUM"] == "false"
    assert env["SOCIALMGR_X__CLIENT_ID"] == "Q2xpZW50SWRFeGFtcGxlOjE6Y2k"
    assert env["SOCIALMGR_PLUGINS__PROVIDERS"] == '["x"]'
    assert env["SOCIALMGR_BACKUP_DIR"] == str(repo / "data/backups")
    assert (repo / "data/socialmgr.sqlite3").exists()
    assert (tmp_path / "Applications/Social Manager.app/Contents/MacOS/social-manager").exists()
    assert macos.scheduled(tmp_path / "LaunchAgents") == macos.DailyRun(7, 30)
    add = ["claude", "mcp", "add", "--scope", "user", "socialmgr", "--"]
    add += claude_code.mcp_command(repo, "/bin/uv")
    assert add in mac.calls
    assert "Connect your X account: run the installer again." in first.output

    # The update path: run again with nothing given. Every answer is kept, nothing is asked.
    mac.registered = repo
    mac.calls.clear()
    again = runner.invoke(app, ["setup", "--yes", "--no-connect", *_hidden(tmp_path, repo)])
    assert again.exit_code == 0, again.output
    assert envfile.read(repo / ".env") == env
    assert macos.scheduled(tmp_path / "LaunchAgents") == macos.DailyRun(7, 30)
    assert not any(c[1:3] == ["mcp", "add"] for c in mac.calls)  # already registered
    assert "Client ID already set" in again.output


def test_setup_interactive_reprompts_bad_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wizard, "system_tz", lambda: "Europe/Belgrade")
    repo = _repo(tmp_path)
    answers = [
        "not a handle!",  # handle: rejected
        "@teammate",  # handle
        "",  # time zone: this Mac's
        "y",  # Premium
        "n",  # open the X portal?
        "short",  # client id: rejected
        "Q2xpZW50SWRFeGFtcGxlOjE6Y2k",  # client id
        "n",  # connect now?
        "25:00",  # daily run time: rejected
        "",  # daily run time: 09:00
        "n",  # open Social Manager now?
    ]
    result = CliRunner().invoke(app, ["setup", *_hidden(tmp_path, repo)], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert "An X handle is" in result.output and "OAuth 2.0 Client ID" in result.output
    assert "Use a 24-hour time" in result.output
    env = envfile.read(repo / ".env")
    assert env["SOCIALMGR_ACCOUNT__HANDLE"] == "@teammate" and env["SOCIALMGR_ACCOUNT__PREMIUM"] == "true"
    assert env["SOCIALMGR_ACCOUNT__TZ"] == "Europe/Belgrade"
    assert macos.scheduled(tmp_path / "LaunchAgents") == macos.DailyRun(9, 0)


def test_setup_without_claude_code_lists_it_as_a_todo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(claude_code, "find", lambda: None)
    repo = _repo(tmp_path)
    result = CliRunner().invoke(
        app,
        ["setup", "--yes", "--handle", "x_user", "--client-id", "Q2xpZW50SWRFeGFtcGxl",
         "--no-connect", "--no-schedule", "--no-app", *_hidden(tmp_path, repo)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Install Claude Code" in result.output and mac.calls == []
    assert not (tmp_path / "Applications").exists() and not (tmp_path / "LaunchAgents").exists()


def test_setup_yes_without_a_handle_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)
    result = CliRunner().invoke(app, ["setup", "--yes", *_hidden(tmp_path, repo)])
    assert result.exit_code != 0 and "Your X handle" in result.output
    assert not (repo / ".env").exists()


# ---------------------------------------------------------------- the background server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_launch_starts_once_and_stop_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _free_port()
    monkeypatch.setenv("SOCIALMGR_PORT", str(port))
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)  # no .env here
    settings = Settings()
    opened: list[str] = []
    assert not server.is_up(settings)
    try:
        assert server.launch(settings, opened.append) == f"http://127.0.0.1:{port}/"
        pid = int(server.pid_file(settings).read_text())
        assert server.is_up(settings)
        server.launch(settings, opened.append)  # already running: just opens the browser
        assert int(server.pid_file(settings).read_text()) == pid and len(opened) == 2
    finally:
        stopped = server.stop(settings)
    assert stopped and not server.is_up(settings) and not server.pid_file(settings).exists()
    assert not server.stop(settings)
    assert server.log_file(settings).exists()


# ---------------------------------------------------------------- the shell scripts


def test_guard_blocks_real_claude_commands(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="real command"):
        claude_code.register_mcp("claude", tmp_path, "/bin/uv")
    with pytest.raises(AssertionError, match="real command"):
        macos.schedule(tmp_path, macos.DailyRun(9, 0), tmp_path / "LaunchAgents")


def test_bootstrap_needs_no_github_account() -> None:
    text = (ROOT / "scripts" / "bootstrap.sh").read_text()
    assert "https://github.com/GemovicNemanja/socials-manager.git" in text
    assert "gh auth" not in text and "gist" not in text


@pytest.mark.parametrize("script", ["install.sh", "bootstrap.sh"])
def test_scripts_parse_and_pass_shellcheck(script: str) -> None:
    path = ROOT / "scripts" / script
    assert os.access(path, os.X_OK)
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    done = subprocess.run(["shellcheck", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


# ---------------------------------------------------------------- a named side-by-side instance


def test_instance_names_never_collide_with_the_main_install() -> None:
    test = Names("test")
    assert (LIVE.app_name, LIVE.run_label, LIVE.mcp_name, LIVE.default_port) == (
        "Social Manager", "local.socialmgr.daily-run", "socialmgr", 8765,
    )  # fmt: skip
    assert (test.app_name, test.app_id, test.run_label, test.mcp_name, test.default_port) == (
        "Social Manager (test)", "local.socialmgr.app.test", "local.socialmgr.daily-run.test",
        "socialmgr-test", 8766,
    )  # fmt: skip
    assert check_instance(" test-2 ") == "test-2" and check_instance("") == ""
    for bad in ("Test", "has space", "-x", "a" * 21):
        with pytest.raises(ValueError):
            check_instance(bad)
    assert [p.name for _, p in wizard.backup_candidates(Path("/nonexistent"), "socialmgr-backups-test")] == []


def test_setup_instance_installs_beside_the_main_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)
    live_app = macos.write_app(tmp_path / "main", tmp_path / "Applications")  # the main install
    macos.schedule(tmp_path / "main", macos.DailyRun(9, 0), tmp_path / "LaunchAgents", load=False)
    runner = CliRunner()
    args = ["--yes", "--handle", "tester_1", "--client-id", "Q2xpZW50SWRFeGFtcGxl", "--no-connect",
            "--run-at", "10:15", *_hidden(tmp_path, repo)]  # fmt: skip
    result = runner.invoke(app, ["setup", *args], env={"SOCIALMGR_INSTANCE": "test"})
    assert result.exit_code == 0, result.output
    env = envfile.read(repo / ".env")
    assert env["SOCIALMGR_INSTANCE"] == "test" and env["SOCIALMGR_PORT"] == "8766"
    assert (tmp_path / "Applications/Social Manager (test).app").is_dir()
    assert macos.scheduled(tmp_path / "LaunchAgents", names=Names("test")) == macos.DailyRun(10, 15)
    # The main install's app and daily run are untouched.
    assert str(tmp_path / "main") in (live_app / "Contents/MacOS/social-manager").read_text()
    assert macos.scheduled(tmp_path / "LaunchAgents") == macos.DailyRun(9, 0)
    touched = {c[5] for c in mac.calls if c[1:3] in (["mcp", "add"], ["mcp", "remove"])}
    assert touched == {"socialmgr-test"}  # never the main install's `socialmgr`
    assert "socialmgr uninstall" in result.output
    # Later runs (updates) remember the instance from .env, without the variable.
    mac.calls.clear()
    again = runner.invoke(app, ["setup", "--yes", "--no-connect", *_hidden(tmp_path, repo)])
    assert again.exit_code == 0, again.output
    assert "Instance “test”" in again.output
    assert {c[5] for c in mac.calls if c[1:3] == ["mcp", "add"]} <= {"socialmgr-test"}


def test_uninstall_removes_only_this_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"SOCIALMGR_INSTANCE=test\nSOCIALMGR_DATA_DIR={tmp_path / 'data'}\n")
    test, repo = Names("test"), wizard.APP_ROOT
    for names in (LIVE, test):
        macos.write_app(repo, names=names)
        macos.schedule(repo, macos.DailyRun(9, 0), load=False, names=names)
    launchctl: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        macos, "_launchctl", lambda *a: launchctl.append(a) or subprocess.CompletedProcess(a, 0)
    )
    fake = FakeClaude(registered_repo=repo)
    monkeypatch.setattr(claude_code, "find", lambda: "claude")
    monkeypatch.setattr(claude_code, "_run", fake)
    result = CliRunner().invoke(app, ["uninstall", "--yes"])
    assert result.exit_code == 0, result.output
    assert not macos.app_path(names=test).exists() and macos.scheduled(names=test) is None
    assert macos.app_path().exists() and macos.scheduled() is not None  # the main install stays
    assert launchctl == [("bootout", f"gui/{os.getuid()}/local.socialmgr.daily-run.test")]
    assert ["claude", "mcp", "remove", "--scope", "user", "socialmgr-test"] in fake.calls
    assert "Delete that folder" in result.output


def test_connect_pauses_a_server_on_the_callback_port_and_restarts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    from types import SimpleNamespace

    from socialmgr.providers.x import connect

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(tmp_path / "data"))
    events: list[str] = []
    holder = server.Holder(4242, tmp_path / "main")
    monkeypatch.setattr(server, "port_free", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(server, "port_holder", lambda port: holder)
    monkeypatch.setattr(server, "pause", lambda h: events.append(f"pause {h.pid}"))
    monkeypatch.setattr(server, "resume", lambda h: events.append(f"resume {h.pid}") or True)
    monkeypatch.setattr(wizard, "_connected", lambda handle: False)

    def fake_connect(*_a: object, **_k: object) -> object:
        events.append("connect")
        return SimpleNamespace(username="tester_1")

    monkeypatch.setattr(connect, "run_connect", fake_connect)
    opts = wizard.Options(repo=tmp_path, yes=True)
    assert wizard._connect(opts, "@tester_1")
    assert events == ["pause 4242", "connect", "resume 4242"]
    # Declining leaves the other server alone and tells the member what to do.
    events.clear()
    monkeypatch.setattr(wizard, "_confirm", lambda *_a, **_k: False)
    assert not wizard._connect(wizard.Options(repo=tmp_path), "@tester_1") and events == []


@pytest.mark.skipif(shutil.which("lsof") is None, reason="needs lsof")
def test_port_holder_finds_a_real_server_and_pause_resume_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = _free_port()
    install = tmp_path / "other install"
    install.mkdir()
    (install / ".venv").symlink_to(ROOT / ".venv")
    (install / ".env").write_text(f"SOCIALMGR_PORT={port}\nSOCIALMGR_DATA_DIR=data\n")
    monkeypatch.chdir(install)
    settings = Settings()
    assert server.port_free(port)
    server.start(settings)
    try:
        assert not server.port_free(port)
        holder = server.port_holder(port)
        assert holder is not None and holder.cwd is not None
        assert holder.pid == int(server.pid_file(settings).read_text())
        assert holder.cwd.resolve() == install.resolve()
        server.pause(holder)
        assert server.port_free(port) and not server.is_up(settings)
        assert server.resume(holder) and server.is_up(settings)
    finally:
        server.stop(settings)
    assert server.port_holder(port) is None
