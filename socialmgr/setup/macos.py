"""The macOS pieces: the Social Manager app (a launcher bundle) and the daily run (a launchd agent)."""

import os
import plistlib
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from socialmgr.setup.names import LIVE, Names

# Where a Finder-launched or launchd process finds git, node and claude: launchd starts with a bare PATH.
_STANDARD_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


def app_path(applications: Path | None = None, names: Names = LIVE) -> Path:
    return (applications or Path.home() / "Applications") / f"{names.app_name}.app"


def agent_path(launch_agents: Path | None = None, names: Names = LIVE) -> Path:
    return (launch_agents or Path.home() / "Library" / "LaunchAgents") / f"{names.run_label}.plist"


def tool_path(extra: list[str] | None = None) -> str:
    """A PATH for background jobs: the dirs of the tools found now, then the standard ones."""
    dirs: list[str] = []
    for tool in ("claude", "git", "node", "uv"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).parent))
    dirs += [str(Path.home() / ".local" / "bin"), *(extra or []), *_STANDARD_PATH]
    return ":".join(dict.fromkeys(dirs))


def _launcher_script(repo: Path, path_env: str, names: Names = LIVE) -> str:
    q = shlex.quote
    cli = repo / ".venv" / "bin" / "socialmgr"
    return f"""#!/bin/bash
# Social Manager launcher (written by `socialmgr setup`; re-run the installer to update it).
export PATH={q(path_env)}
REPO={q(str(repo))}
alert() {{
  /usr/bin/osascript -e "display alert \\"{names.app_name}\\" message \\"$1\\"" >/dev/null 2>&1
}}
if ! cd "$REPO" 2>/dev/null; then
  alert "The Social Manager folder is missing ($REPO). Run the installer again."
  exit 1
fi
mkdir -p data/logs
if ! {q(str(cli))} launch >>data/logs/launcher.log 2>&1; then
  alert "Social Manager couldn't start. Details: $REPO/data/logs/launcher.log"
  exit 1
fi
"""


def write_app(
    repo: Path, applications: Path | None = None, icon: Path | None = None, names: Names = LIVE
) -> Path:
    """(Re)write the launcher app. Opening it starts the server if needed and opens the dashboard."""
    app = app_path(applications, names)
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    exe = macos / "social-manager"
    exe.write_text(_launcher_script(repo, tool_path(), names), encoding="utf-8")
    exe.chmod(0o755)
    info: dict[str, object] = {
        "CFBundleName": names.app_name,
        "CFBundleDisplayName": names.app_name,
        "CFBundleIdentifier": names.app_id,
        "CFBundleExecutable": exe.name,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "1.0",
        "LSUIElement": True,  # a launcher: no Dock icon, it opens the browser and exits
        "LSMinimumSystemVersion": "12.0",
    }
    if icon is not None and icon.exists():
        shutil.copyfile(icon, resources / "AppIcon.icns")
        info["CFBundleIconFile"] = "AppIcon"
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    return app


@dataclass(frozen=True)
class DailyRun:
    hour: int
    minute: int


def agent_plist(repo: Path, at: DailyRun, names: Names = LIVE) -> dict[str, object]:
    log = repo / "data" / "logs" / "daily-run.log"
    return {
        "Label": names.run_label,
        # `--scheduled` skips the start when a run already finished since the last scheduled time
        # and waits for the network (a Mac just woken often has none for a few seconds).
        "ProgramArguments": [
            str(repo / ".venv" / "bin" / "socialmgr"),
            "run",
            "--scheduled",
            f"{at.hour:02d}:{at.minute:02d}",
        ],
        "WorkingDirectory": str(repo),
        # At the time, or on wake when the Mac slept through it; a run already in progress is left alone.
        "StartCalendarInterval": {"Hour": at.hour, "Minute": at.minute},
        # And at every login (and when installed): catches up a day the Mac was off at that time.
        "RunAtLoad": True,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {"PATH": tool_path()},
        "ProcessType": "Background",
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def schedule(
    repo: Path, at: DailyRun, launch_agents: Path | None = None, load: bool = True, names: Names = LIVE
) -> Path:
    """Install (or move) the daily run. Re-running replaces the previous schedule."""
    path = agent_path(launch_agents, names)
    path.parent.mkdir(parents=True, exist_ok=True)
    (repo / "data" / "logs").mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(agent_plist(repo, at, names), f)
    if load:
        domain = f"gui/{os.getuid()}"
        _launchctl("bootout", f"{domain}/{names.run_label}")
        done = _launchctl("bootstrap", domain, str(path))
        if done.returncode != 0:
            raise RuntimeError(f"launchctl couldn't load the daily run: {done.stderr.strip()}")
    return path


def unschedule(launch_agents: Path | None = None, load: bool = True, names: Names = LIVE) -> bool:
    path = agent_path(launch_agents, names)
    if load:
        _launchctl("bootout", f"gui/{os.getuid()}/{names.run_label}")
    if path.exists():
        path.unlink()
        return True
    return False


def scheduled(launch_agents: Path | None = None, names: Names = LIVE) -> DailyRun | None:
    path = agent_path(launch_agents, names)
    if not path.exists():
        return None
    with path.open("rb") as f:
        when = plistlib.load(f).get("StartCalendarInterval") or {}
    return DailyRun(int(when.get("Hour", 0)), int(when.get("Minute", 0)))


def remove_app(applications: Path | None = None, names: Names = LIVE) -> bool:
    app = app_path(applications, names)
    if app.exists():
        shutil.rmtree(app)
        return True
    return False


def notify(
    title: str, message: str, run: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> bool:
    """A macOS notification (Notification Center). False where there's no osascript (not a Mac)."""
    if run is None and (os.environ.get("PYTEST_CURRENT_TEST") or not Path("/usr/bin/osascript").exists()):
        return False  # never pop real notifications from the test suite

    def quote(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"')[:400] + '"'

    script = f"display notification {quote(message)} with title {quote(title)}"
    done = (run or subprocess.run)(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    return done.returncode == 0
