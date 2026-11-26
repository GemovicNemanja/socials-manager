"""The macOS pieces: the Social Manager app (a launcher bundle) and the daily run (a launchd agent)."""

import os
import plistlib
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Social Manager"
APP_ID = "local.socialmgr.app"
RUN_LABEL = "local.socialmgr.daily-run"
# Where a Finder-launched or launchd process finds git, node and claude: launchd starts with a bare PATH.
_STANDARD_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


def app_path(applications: Path | None = None) -> Path:
    return (applications or Path.home() / "Applications") / f"{APP_NAME}.app"


def agent_path(launch_agents: Path | None = None) -> Path:
    return (launch_agents or Path.home() / "Library" / "LaunchAgents") / f"{RUN_LABEL}.plist"


def tool_path(extra: list[str] | None = None) -> str:
    """A PATH for background jobs: the dirs of the tools found now, then the standard ones."""
    dirs: list[str] = []
    for tool in ("claude", "git", "node", "uv"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).parent))
    dirs += [str(Path.home() / ".local" / "bin"), *(extra or []), *_STANDARD_PATH]
    return ":".join(dict.fromkeys(dirs))


def _launcher_script(repo: Path, path_env: str) -> str:
    q = shlex.quote
    cli = repo / ".venv" / "bin" / "socialmgr"
    return f"""#!/bin/bash
# Social Manager launcher (written by `socialmgr setup`; re-run the installer to update it).
export PATH={q(path_env)}
REPO={q(str(repo))}
alert() {{
  /usr/bin/osascript -e "display alert \\"{APP_NAME}\\" message \\"$1\\"" >/dev/null 2>&1
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


def write_app(repo: Path, applications: Path | None = None, icon: Path | None = None) -> Path:
    """(Re)write the launcher app. Opening it starts the server if needed and opens the dashboard."""
    app = app_path(applications)
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    exe = macos / "social-manager"
    exe.write_text(_launcher_script(repo, tool_path()), encoding="utf-8")
    exe.chmod(0o755)
    info: dict[str, object] = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": APP_ID,
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


def agent_plist(repo: Path, at: DailyRun) -> dict[str, object]:
    log = repo / "data" / "logs" / "daily-run.log"
    return {
        "Label": RUN_LABEL,
        "ProgramArguments": [str(repo / ".venv" / "bin" / "socialmgr"), "run"],
        "WorkingDirectory": str(repo),
        # A missed time (Mac asleep) runs on wake; a run already in progress is left alone.
        "StartCalendarInterval": {"Hour": at.hour, "Minute": at.minute},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {"PATH": tool_path()},
        "ProcessType": "Background",
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def schedule(repo: Path, at: DailyRun, launch_agents: Path | None = None, load: bool = True) -> Path:
    """Install (or move) the daily run. Re-running replaces the previous schedule."""
    path = agent_path(launch_agents)
    path.parent.mkdir(parents=True, exist_ok=True)
    (repo / "data" / "logs").mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(agent_plist(repo, at), f)
    if load:
        domain = f"gui/{os.getuid()}"
        _launchctl("bootout", f"{domain}/{RUN_LABEL}")
        done = _launchctl("bootstrap", domain, str(path))
        if done.returncode != 0:
            raise RuntimeError(f"launchctl couldn't load the daily run: {done.stderr.strip()}")
    return path


def unschedule(launch_agents: Path | None = None, load: bool = True) -> bool:
    path = agent_path(launch_agents)
    if load:
        _launchctl("bootout", f"gui/{os.getuid()}/{RUN_LABEL}")
    if path.exists():
        path.unlink()
        return True
    return False


def scheduled(launch_agents: Path | None = None) -> DailyRun | None:
    path = agent_path(launch_agents)
    if not path.exists():
        return None
    with path.open("rb") as f:
        when = plistlib.load(f).get("StartCalendarInterval") or {}
    return DailyRun(int(when.get("Hour", 0)), int(when.get("Minute", 0)))
