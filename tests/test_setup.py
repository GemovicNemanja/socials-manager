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
    real_shape = "Q2xpZW50SWRFeGFtcGxlOjE6Y2k"  # base64 of "ClientIdExample:1:ci", like X's
    assert wizard.check_client_id(f" {real_shape} ") == real_shape
    for wrong in (
        "short",
        "Q2xpZW50SWRFeGFtcGxl",  # base64, but not a Client ID
        "AAAAAAAAAAAAAAAAAAAAAMLheAAAAAAA0%2BuSeid%2BULvsea4JtiGRiSDSJSI%3DEUifiRBkKG5E2X",  # bearer-like
        "x" * 108,  # a long token
        "aBc123-_xyz:ci",  # not base64 at all
    ):
        with pytest.raises(ValueError, match="Keys and tokens"):
            wizard.check_client_id(wrong)
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
    assert plist["ProgramArguments"] == [str(repo / ".venv/bin/socialmgr"), "run", "--scheduled", "07:30"]
    assert plist["RunAtLoad"] is True  # at login: catches up a day the Mac was off at 07:30
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
        ["setup", "--yes", "--handle", "x_user", "--client-id", "Q2xpZW50SWRFeGFtcGxlOjE6Y2k",
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
    args = ["--yes", "--handle", "tester_1", "--client-id", "Q2xpZW50SWRFeGFtcGxlOjE6Y2k", "--no-connect",
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


def test_rerun_replaces_a_saved_key_that_is_not_a_client_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    repo = _repo(tmp_path)
    envfile.update(
        repo / ".env", {"SOCIALMGR_ACCOUNT__HANDLE": "@tester_1", "SOCIALMGR_X__CLIENT_ID": "x" * 108}
    )
    answers = ["", "n", "n", "Q2xpZW50SWRFeGFtcGxlOjE6Y2k", "n", "", "n"]  # tz, premium, portal, id, ...
    result = CliRunner().invoke(app, ["setup", *_hidden(tmp_path, repo)], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert "The saved Client ID is a different key" in result.output
    assert envfile.read(repo / ".env")["SOCIALMGR_X__CLIENT_ID"] == "Q2xpZW50SWRFeGFtcGxlOjE6Y2k"


def test_notify_quotes_text_for_osascript() -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert macos.notify('Run "stopped"', "X refused \\ the token", run=fake)
    script = calls[0][2]
    assert calls[0][:2] == ["/usr/bin/osascript", "-e"]
    assert script == 'display notification "X refused \\\\ the token" with title "Run \\"stopped\\""'
    assert not macos.notify("t", "m")  # inside pytest: never a real notification


def test_a_failed_scheduled_run_notifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from socialmgr.services import runs

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(tmp_path / "data"))
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        macos, "notify", lambda title, message, run=None: sent.append((title, message)) or True
    )

    def fail(*_a: object, **_k: object) -> object:
        raise runs.StepFailed(7, "collect", "X refused to refresh the token (400)")

    monkeypatch.setattr(runs, "run_now", fail)
    result = CliRunner().invoke(app, ["run"])  # CliRunner's stdout isn't a terminal, like launchd's
    assert result.exit_code == 1
    assert sent and "stopped" in sent[0][0] and "refresh the token" in sent[0][1]


def test_instance_setup_warns_that_connecting_signs_out_the_main_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: FakeClaude
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wizard, "_connected", lambda handle: False)
    monkeypatch.setattr(wizard, "_confirm", lambda *_a, **_k: False)
    warnings: list[str] = []
    monkeypatch.setattr(wizard, "_warn", warnings.append)
    assert not wizard._connect(wizard.Options(repo=tmp_path, names=Names("test")), "@tester_1")
    assert any("signs your main install out" in w for w in warnings)
    warnings.clear()
    assert not wizard._connect(wizard.Options(repo=tmp_path), "@tester_1")  # the main install: no warning
    assert not any("signs your main install out" in w for w in warnings)


# ---------------------------------------------------------------- catching up missed runs


def test_last_slot() -> None:
    from datetime import datetime, timedelta, timezone

    from socialmgr.setup.catchup import last_slot

    tz = timezone(timedelta(hours=1))
    at = macos.DailyRun(9, 0)
    assert last_slot(at, datetime(2026, 11, 27, 11, 5, tzinfo=tz)) == datetime(2026, 11, 27, 9, 0, tzinfo=tz)
    assert last_slot(at, datetime(2026, 11, 27, 8, 59, tzinfo=tz)) == datetime(2026, 11, 26, 9, 0, tzinfo=tz)
    assert last_slot(at, datetime(2026, 11, 27, 9, 0, tzinfo=tz)) == datetime(2026, 11, 27, 9, 0, tzinfo=tz)


def test_wait_for_network_retries_until_x_answers() -> None:
    from socialmgr.setup.catchup import wait_for_network

    now = [0.0]
    attempts: list[object] = []

    def connect(addr: object, timeout: float) -> object:
        attempts.append(addr)
        if len(attempts) < 3:
            raise OSError("no route")
        return None

    def sleep(s: float) -> None:
        now[0] += s

    assert wait_for_network(timeout_s=600, every_s=15, connect=connect, sleep=sleep, clock=lambda: now[0])
    assert len(attempts) == 3 and now[0] == 30
    now[0] = 0.0

    def never(addr: object, timeout: float) -> object:
        raise OSError("offline")

    assert not wait_for_network(timeout_s=60, every_s=15, connect=never, sleep=sleep, clock=lambda: now[0])


def _add_run(settings: Settings, finished_at: object) -> None:
    from datetime import UTC, datetime

    from socialmgr.db import migrate
    from socialmgr.db.models import Run
    from socialmgr.db.session import make_engine, session_factory

    engine = make_engine(settings)
    migrate.upgrade(engine)
    with session_factory(engine)() as session:
        t = datetime.now(UTC)
        session.add(Run(trigger="cli", started_at=t, cutoff_at=t, finished_at=finished_at, status="finished"))
        session.commit()


def test_scheduled_run_skips_when_it_already_ran_and_catches_up_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from socialmgr.services import runs
    from socialmgr.setup import catchup

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(tmp_path / "data"))
    started: list[str] = []

    def fake_run_now(*_a: object, trigger: str = "cli", **_k: object) -> list[object]:
        started.append(trigger)
        return []

    monkeypatch.setattr(runs, "run_now", fake_run_now)
    monkeypatch.setattr(catchup, "wait_for_network", lambda: True)
    hour_ago = datetime.now().astimezone() - timedelta(hours=1)
    slot = f"{hour_ago.hour:02d}:{hour_ago.minute:02d}"  # the most recent slot was an hour ago

    # A run finished a day ago: the slot was missed (Mac off), so the login start catches up.
    _add_run(Settings(), datetime.now(UTC) - timedelta(days=1))
    result = CliRunner().invoke(app, ["run", "--scheduled", slot])
    assert result.exit_code == 0, result.output
    assert started == ["schedule"]

    # A run finished just now: the next login (or wake) start does nothing.
    _add_run(Settings(), datetime.now(UTC))
    again = CliRunner().invoke(app, ["run", "--scheduled", slot])
    assert again.exit_code == 0 and "nothing to catch up" in again.output and started == ["schedule"]

    # By hand, `socialmgr run` always runs.
    CliRunner().invoke(app, ["run"])
    assert started == ["schedule", "cli"]


# ---------------------------------------------------------------- auto-update


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}  # fmt: skip
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True)
    return done.stdout.strip()


def _commit(repo: Path, path: str, text: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"change {path}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def remote_and_install(tmp_path: Path) -> tuple[Path, Path]:
    """A 'GitHub' repo (main) and a member's install cloned from it."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _commit(upstream, "socialmgr/app.py", "v1")
    _commit(upstream, "web/src/main.ts", "v1")
    install = tmp_path / "install"
    _git(tmp_path, "clone", "-q", str(upstream), str(install))
    (install / "data").mkdir()
    (install / "data" / "socialmgr.sqlite3").write_text("member data")  # untracked, like the real one
    return upstream, install


class Tools:
    """Records uv/pnpm calls; `fail` names a step that fails once."""

    def __init__(self, fail: str = "") -> None:
        self.calls: list[str] = []
        self.fail = fail

    def __call__(self, cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        step = "build" if "build" in cmd else "sync" if "sync" in cmd else "pnpm-install"
        self.calls.append(f"{step}@{(cwd / 'socialmgr/app.py').read_text() if step == 'sync' else cwd.name}")
        code = 1 if self.fail and self.fail == step else 0
        if code:
            self.fail = ""  # the new version fails; the old one builds again
        return subprocess.CompletedProcess(list(cmd), code, "", "boom" if code else "")


def test_auto_update_fast_forwards_and_rebuilds_only_what_changed(
    remote_and_install: tuple[Path, Path],
) -> None:
    from socialmgr.setup import update

    upstream, install = remote_and_install
    tools = Tools()
    assert update.auto_update(install, tools).status == "current" and tools.calls == []

    new = _commit(upstream, "socialmgr/app.py", "v2")  # backend only
    result = update.auto_update(install, tools)
    assert (result.status, result.new) == ("updated", new)
    assert (install / "socialmgr/app.py").read_text() == "v2" and tools.calls == ["sync@v2"]
    assert (install / "data/socialmgr.sqlite3").read_text() == "member data"

    tools.calls.clear()
    _commit(upstream, "web/src/main.ts", "v2")  # the web app changed: rebuild it too
    assert update.auto_update(install, tools).status == "updated"
    assert tools.calls == ["sync@v2", "pnpm-install@web", "build@web"]


def test_auto_update_rolls_back_when_the_new_version_fails_to_build(
    remote_and_install: tuple[Path, Path],
) -> None:
    from socialmgr.setup import update

    upstream, install = remote_and_install
    old = _git(install, "rev-parse", "HEAD")
    _commit(upstream, "socialmgr/app.py", "v2")
    _commit(upstream, "web/src/main.ts", "v2")
    tools = Tools(fail="build")
    result = update.auto_update(install, tools)
    assert result.status == "failed" and "kept the previous version" in result.detail
    assert _git(install, "rev-parse", "HEAD") == old and (install / "socialmgr/app.py").read_text() == "v1"
    assert tools.calls[:3] == ["sync@v2", "pnpm-install@web", "build@web"]
    assert tools.calls[3] == "sync@v1"  # the old version reinstalled
    assert (install / "data/socialmgr.sqlite3").read_text() == "member data"


@pytest.mark.parametrize("situation", ["edited", "branch", "diverged"])
def test_auto_update_leaves_a_customised_install_alone(
    remote_and_install: tuple[Path, Path], situation: str
) -> None:
    from socialmgr.setup import update

    upstream, install = remote_and_install
    _commit(upstream, "socialmgr/app.py", "v2")
    if situation == "edited":
        (install / "socialmgr/app.py").write_text("my local change")
    elif situation == "branch":
        _git(install, "checkout", "-q", "-b", "experiment")
    else:
        _commit(install, "socialmgr/other.py", "mine")
    before = _git(install, "rev-parse", "HEAD")
    tools = Tools()
    result = update.auto_update(install, tools)
    assert result.status == "skipped" and tools.calls == []
    assert _git(install, "rev-parse", "HEAD") == before
    if situation == "edited":
        assert (install / "socialmgr/app.py").read_text() == "my local change"


def test_scheduled_run_updates_first_and_notifies_when_it_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from socialmgr.services import runs
    from socialmgr.setup import catchup, update

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOCIALMGR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(catchup, "wait_for_network", lambda: True)
    monkeypatch.setattr(runs, "run_now", lambda *_a, **_k: [])
    sent: list[str] = []
    monkeypatch.setattr(macos, "notify", lambda title, message, run=None: sent.append(message) or True)
    monkeypatch.setattr(
        update,
        "auto_update",
        lambda repo, *a, **k: update.UpdateResult("skipped", detail="files were edited"),
    )
    result = CliRunner().invoke(app, ["run", "--scheduled", "00:00"])
    assert result.exit_code == 0, result.output
    assert "Update: skipped files were edited" in result.output and sent == ["files were edited"]

    sent.clear()  # offline: no notification (the run itself will say if X is unreachable)
    monkeypatch.setattr(
        update,
        "auto_update",
        lambda repo, *a, **k: update.UpdateResult("skipped", detail="couldn't reach GitHub"),
    )
    assert CliRunner().invoke(app, ["run", "--scheduled", "00:00"]).exit_code == 0 and sent == []

    monkeypatch.setenv("SOCIALMGR_RUN__AUTO_UPDATE", "false")  # turned off: never called
    monkeypatch.setattr(update, "auto_update", lambda *a, **k: pytest.fail("updated while turned off"))
    assert CliRunner().invoke(app, ["run", "--scheduled", "00:00"]).exit_code == 0
