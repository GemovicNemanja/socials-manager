"""``socialmgr setup``: the guided, re-runnable part of the installer.

Every step checks what is already done and keeps it, so running the installer again is how a team
member updates (``--reconfigure`` asks every question again).
"""

import os
import re
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from socialmgr.setup import claude_code, envfile, macos, server

APP_ROOT = Path(__file__).resolve().parents[2]
X_PORTAL = "https://developer.x.com/en/portal/dashboard"
PLACEHOLDER_HANDLE = "@your_account"
_HANDLE = re.compile(r"^@?[A-Za-z0-9_]{1,15}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_\-:.]{10,}$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class Options:
    repo: Path = APP_ROOT
    uv: str | None = None
    handle: str | None = None
    tz: str | None = None
    premium: bool | None = None
    client_id: str | None = None
    backup_dir: str | None = None
    run_at: str | None = None
    connect: bool = True
    mcp: bool = True
    app: bool = True
    schedule: bool = True
    first_run: bool = True
    reconfigure: bool = False
    yes: bool = False  # never prompt: take given values, kept values or defaults
    applications: Path | None = None
    launch_agents: Path | None = None
    load_agent: bool = True
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- validation (pure)


def normalize_handle(value: str) -> str:
    value = value.strip()
    if not _HANDLE.match(value):
        raise ValueError("An X handle is 1-15 letters, digits or underscores, like @your_name.")
    return "@" + value.lstrip("@")


def check_tz(value: str) -> str:
    try:
        ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{value!r} isn't a time zone name like Europe/Belgrade.") from exc
    return value.strip()


def check_client_id(value: str) -> str:
    value = value.strip()
    if not _CLIENT_ID.match(value):
        raise ValueError("That doesn't look like an OAuth 2.0 Client ID (a long run of letters and digits).")
    return value


def parse_time(value: str) -> macos.DailyRun:
    m = _TIME.match(value.strip())
    if not m:
        raise ValueError("Use a 24-hour time like 09:00.")
    return macos.DailyRun(int(m.group(1)), int(m.group(2)))


def system_tz() -> str:
    target = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in target:
        name = target.split("zoneinfo/", 1)[1]
        try:
            return check_tz(name)
        except ValueError:
            pass
    return "UTC"


def backup_candidates(home: Path | None = None) -> list[tuple[str, Path]]:
    """Synced folders worth backing up into, as (label, folder) pairs."""
    home = home or Path.home()
    out: list[tuple[str, Path]] = []
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        for d in sorted(cloud.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                out.append((d.name.split("-")[0], d / "socialmgr-backups"))
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.is_dir():
        out.append(("iCloud Drive", icloud / "socialmgr-backups"))
    return out


# ---------------------------------------------------------------- prompting


def _say(text: str = "") -> None:
    typer.echo(text)


def _step(n: int, total: int, title: str) -> None:
    typer.secho(f"\n[{n}/{total}] {title}", bold=True)


def _ok(text: str) -> None:
    typer.secho(f"  ✓ {text}", fg=typer.colors.GREEN)


def _warn(text: str) -> None:
    typer.secho(f"  ! {text}", fg=typer.colors.YELLOW)


def _ask(opts: Options, label: str, default: str | None, validate: Callable[[str], object] = str) -> str:
    """Prompt until ``validate`` accepts the answer; with --yes take the default (or fail)."""
    if opts.yes:
        if default is None:
            raise typer.BadParameter(f"{label}: no value given (pass it as an option, or run without --yes).")
        return str(validate(default))
    while True:
        answer = typer.prompt(f"  {label}", default=default) if default else typer.prompt(f"  {label}")
        try:
            return str(validate(answer))
        except ValueError as exc:
            _warn(str(exc))


def _confirm(opts: Options, label: str, default: bool = True) -> bool:
    return default if opts.yes else typer.confirm(f"  {label}", default=default)


# ---------------------------------------------------------------- steps


def _account(opts: Options, env: dict[str, str]) -> dict[str, str]:
    kept = env.get("SOCIALMGR_ACCOUNT__HANDLE", "")
    if opts.handle:
        handle = normalize_handle(opts.handle)
    elif kept and kept != PLACEHOLDER_HANDLE and not opts.reconfigure:
        handle = normalize_handle(kept)
    else:
        handle = _ask(opts, "Your X handle", None, normalize_handle)
    configured = bool(kept) and kept != PLACEHOLDER_HANDLE and not opts.reconfigure
    if opts.tz:
        tz = check_tz(opts.tz)
    elif configured and env.get("SOCIALMGR_ACCOUNT__TZ"):
        tz = check_tz(env["SOCIALMGR_ACCOUNT__TZ"])
    else:
        tz = _ask(opts, "Your time zone", system_tz(), check_tz)
    if opts.premium is not None:
        premium = opts.premium
    elif "SOCIALMGR_ACCOUNT__PREMIUM" in env and not opts.reconfigure:
        premium = env["SOCIALMGR_ACCOUNT__PREMIUM"].lower() == "true"
    else:
        premium = _confirm(opts, "Do you pay for X Premium (blue check)?", default=False)
    _ok(f"{handle}, {tz}, {'Premium' if premium else 'no Premium'}")
    return {
        "SOCIALMGR_ACCOUNT__HANDLE": handle,
        "SOCIALMGR_ACCOUNT__TZ": tz,
        "SOCIALMGR_ACCOUNT__PREMIUM": "true" if premium else "false",
    }


def _x_app(opts: Options, env: dict[str, str]) -> dict[str, str]:
    kept = env.get("SOCIALMGR_X__CLIENT_ID", "")
    if opts.client_id:
        client_id = check_client_id(opts.client_id)
    elif kept and not opts.reconfigure:
        client_id = check_client_id(kept)
        _ok("Client ID already set")
        return {"SOCIALMGR_X__CLIENT_ID": client_id}
    else:
        _say(
            "  Social Manager reads your posts through your own X developer app. Create it once:\n"
            "    1. In the X developer portal, create a project and an app.\n"
            "    2. In the app's User authentication settings choose OAuth 2.0, type Native App,\n"
            "       permissions Read.\n"
            "    3. Callback URI: http://127.0.0.1:8765/auth/x/callback\n"
            "       Website URL: your X profile (https://x.com/<your handle>) is fine.\n"
            "    4. Copy the OAuth 2.0 Client ID (you don't need the secret, API key or bearer token).\n"
            "    5. Under Billing, buy a few dollars of credit and set a spending limit. Daily runs\n"
            "       cost about $5 a month; Social Manager also stops at its own monthly cap."
        )
        if not opts.yes and _confirm(opts, "Open the X developer portal in your browser?"):
            webbrowser.open(X_PORTAL)
        client_id = _ask(opts, "OAuth 2.0 Client ID", None, check_client_id)
    _ok("Client ID saved")
    return {"SOCIALMGR_X__CLIENT_ID": client_id}


def _backups(opts: Options, env: dict[str, str]) -> dict[str, str]:
    local = opts.repo / "data" / "backups"
    if opts.backup_dir is not None:
        chosen = Path(opts.backup_dir).expanduser()
    elif env.get("SOCIALMGR_BACKUP_DIR") and not opts.reconfigure:
        _ok(f"Backups go to {env['SOCIALMGR_BACKUP_DIR']}")
        return {}
    else:
        options: list[tuple[str, Path]] = [("This Mac only", local), *backup_candidates()]
        chosen = local
        if not opts.yes and len(options) > 1:
            _say("  Every run backs up your database. Where should backups go?")
            for i, (label, path) in enumerate(options, 1):
                _say(f"    {i}. {label}  ({path})")
            pick = _ask(opts, "Choose", "2", lambda v: _pick(v, len(options)))
            chosen = options[int(pick) - 1][1]
    chosen.mkdir(parents=True, exist_ok=True)
    _ok(f"Backups go to {chosen}")
    return {"SOCIALMGR_BACKUP_DIR": str(chosen)}


def _pick(value: str, n: int) -> str:
    if not value.strip().isdigit() or not 1 <= int(value) <= n:
        raise ValueError(f"Choose a number from 1 to {n}.")
    return value.strip()


def _connected(handle: str) -> bool:
    from sqlalchemy import select

    from socialmgr.core.config import Settings
    from socialmgr.db.models import Credential
    from socialmgr.db.session import make_engine, session_factory
    from socialmgr.providers.x.connect import normalize_handle as norm
    from socialmgr.services.pipeline import owner_account

    settings = Settings()
    with session_factory(make_engine(settings))() as session:
        owner = owner_account(session, settings.workspace_id)
        if owner is None or owner.external_id is None or norm(owner.handle) != norm(handle):
            return False
        cred = session.scalar(select(Credential.id).where(Credential.account_id == owner.id).limit(1))
        return cred is not None


def _connect(opts: Options, handle: str) -> bool:
    import httpx

    from socialmgr.core.config import Settings
    from socialmgr.db.session import make_engine, session_factory
    from socialmgr.providers.x.connect import run_connect
    from socialmgr.providers.x.errors import XError

    if _connected(handle) and not opts.reconfigure:
        _ok(f"{handle} is connected")
        return True
    if not opts.connect:
        opts.todo.append("Connect your X account: run the installer again.")
        _warn("Skipped (--no-connect).")
        return False
    if not _confirm(opts, f"Open X in your browser to connect {handle} now?"):
        opts.todo.append("Connect your X account: run the installer again.")
        return False
    settings = Settings()
    if server.stop(settings):
        _say("  (Stopped the running Social Manager server: connecting needs its port.)")
    try:
        with httpx.Client(timeout=30.0) as http:
            result = run_connect(settings, session_factory(make_engine(settings)), http=http, echo=_say)
    except XError as exc:
        _warn(str(exc))
        opts.todo.append("Connect your X account: run the installer again.")
        return False
    _ok(f"Connected @{result.username}")
    return True


def _claude(opts: Options, uv: str) -> None:
    claude = claude_code.find()
    if claude is None:
        _warn("Claude Code isn't installed: post labels and the MCP tools need it.")
        opts.todo.append("Install Claude Code (claude.com/claude-code), then run the installer again.")
        return
    if not claude_code.signed_in(claude):
        if not opts.yes and _confirm(opts, "Claude Code isn't signed in. Sign in now (opens your browser)?"):
            subprocess.run([claude, "auth", "login"], check=False)
        if not claude_code.signed_in(claude):
            opts.todo.append("Sign in to Claude Code: run `claude` once in Terminal.")
            _warn("Not signed in yet: labels wait until you are.")
    if not opts.mcp:
        _warn("MCP registration skipped (--no-mcp).")
        return
    if claude_code.mcp_registered(claude, opts.repo) and not opts.reconfigure:
        _ok("MCP server `socialmgr` is registered for every Claude Code session")
        return
    claude_code.register_mcp(claude, opts.repo, uv)
    _ok("MCP server `socialmgr` registered for every Claude Code session")


def _mac(opts: Options) -> None:
    if sys.platform != "darwin":
        _warn("Not a Mac: skipping the app and the daily run.")
        return
    if opts.app:
        icon = opts.repo / "scripts" / "assets" / "AppIcon.icns"
        app = macos.write_app(opts.repo, opts.applications, icon)
        _ok(f"App: {app} (Spotlight: “Social Manager”)")
    if not opts.schedule:
        return
    current = macos.scheduled(opts.launch_agents)
    if opts.run_at:
        at = parse_time(opts.run_at)
    elif current is not None and not opts.reconfigure:
        at = current
    else:
        _say("  A daily run collects your posts and followers, scores forecasts and backs up.")
        at = parse_time(_ask(opts, "Run it every day at (24h, your time)", "09:00", parse_time_text))
    macos.schedule(opts.repo, at, opts.launch_agents, load=opts.load_agent)
    _ok(f"Daily run at {at.hour:02d}:{at.minute:02d} (runs on wake if the Mac was asleep)")


def parse_time_text(value: str) -> str:
    parse_time(value)
    return value.strip()


def _has_runs() -> bool:
    from sqlalchemy import select

    from socialmgr.core.config import Settings
    from socialmgr.db.models import Run
    from socialmgr.db.session import make_engine, session_factory

    settings = Settings()
    with session_factory(make_engine(settings))() as session:
        return session.scalar(select(Run.id).where(Run.status == "finished").limit(1)) is not None


def run_setup(opts: Options) -> int:
    os.chdir(opts.repo)  # .env and data/ are read from the repository folder
    from socialmgr.core.config import Settings
    from socialmgr.db import migrate
    from socialmgr.db.session import make_engine

    uv = opts.uv or _find_uv()
    total = 6
    env_path = opts.repo / ".env"
    env = envfile.read(env_path)

    _step(1, total, "Your X account")
    values = _account(opts, env)
    _step(2, total, "Your X developer app")
    values |= _x_app(opts, env)
    _step(3, total, "Backups")
    values |= _backups(opts, env)
    values["SOCIALMGR_PLUGINS__PROVIDERS"] = '["x"]'
    envfile.update(env_path, values, template=opts.repo / ".env.example")
    settings = Settings()
    migrate.upgrade(make_engine(settings))
    _ok(f"Settings in {env_path}; database at {settings.db_path}")

    _step(4, total, "Connect X")
    connected = _connect(opts, values["SOCIALMGR_ACCOUNT__HANDLE"])

    _step(5, total, "Claude Code and MCP")
    _claude(opts, uv)

    _step(6, total, "The Social Manager app and the daily run")
    _mac(opts)

    first = connected and opts.first_run and not _has_runs()
    if first and _confirm(opts, "Run the first collection now (about $0.05-0.20 of X credit)?"):
        subprocess.run([sys.executable, "-m", "socialmgr", "run"], check=False)

    typer.secho("\nSocial Manager is set up.", bold=True, fg=typer.colors.GREEN)
    if sys.platform == "darwin" and opts.app:
        _say("Open it any time: Spotlight (⌘ Space) → “Social Manager”, or ~/Applications.")
    _say("In Claude Code, try: “Use the socialmgr weekly_review prompt”.")
    _say("To update later, run the same install command again.")
    for item in opts.todo:
        _warn(item)
    if sys.platform == "darwin" and opts.app and not opts.yes and _confirm(opts, "Open Social Manager now?"):
        server.launch(Settings(), webbrowser.open)
    return 0


def _find_uv() -> str:
    import shutil

    found = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(found).exists():
        raise typer.BadParameter("uv not found; run scripts/install.sh, which installs it.")
    return found
