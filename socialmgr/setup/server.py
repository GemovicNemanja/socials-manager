"""Start, find and stop the local web server in the background (for the Social Manager app)."""

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from socialmgr.core.config import Settings


def url(settings: Settings) -> str:
    return f"http://{settings.host}:{settings.port}/"


def pid_file(settings: Settings) -> Path:
    return settings.data_dir / "serve.pid"


def log_file(settings: Settings) -> Path:
    return settings.data_dir / "logs" / "serve.log"


def is_up(settings: Settings, timeout: float = 1.0) -> bool:
    """A Social Manager server answers on the configured port."""
    try:
        r = httpx.get(f"{url(settings)}api/health", timeout=timeout)
    except httpx.HTTPError:
        return False
    return r.status_code == 200 and r.json().get("status") == "ok"


def _our_pid(settings: Settings) -> int | None:
    """The pid in the pid file, if that process is still a socialmgr server."""
    path = pid_file(settings)
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    if cmd.returncode != 0 or "socialmgr" not in cmd.stdout:
        path.unlink(missing_ok=True)
        return None
    return pid


def start(settings: Settings, wait_s: float = 45.0) -> int:
    """Start ``socialmgr serve`` detached from this process; wait until it answers. Returns its pid."""
    log = log_file(settings)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "socialmgr", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # keeps running after the app's launcher exits
        )
    pid_file(settings).write_text(str(proc.pid))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"The server stopped while starting; see {log}.")
        if is_up(settings):
            return proc.pid
        time.sleep(0.3)
    raise RuntimeError(f"The server didn't answer within {wait_s:.0f} s; see {log}.")


def stop(settings: Settings, wait_s: float = 10.0) -> bool:
    """Stop the server this app started. False if none was running."""
    pid = _our_pid(settings)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and _our_pid(settings) is not None:
        time.sleep(0.2)
    if _our_pid(settings) is not None:
        os.kill(pid, signal.SIGKILL)
    pid_file(settings).unlink(missing_ok=True)
    return True


def launch(settings: Settings, open_url: Callable[[str], object]) -> str:
    """Open the dashboard, starting the server first when it isn't running."""
    if not is_up(settings):
        start(settings)
    open_url(url(settings))
    return url(settings)


# ---------------------------------------------------------------- the X callback port
# Every install receives the X sign-in callback on the same port (it must match the X app), so
# connecting may need to pause a Social Manager server that is running there, possibly another
# install's (a test instance connecting next to the real one).


@dataclass(frozen=True)
class Holder:
    pid: int
    cwd: Path | None  # the install folder it was started from, to restart it afterwards


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def port_holder(port: int) -> Holder | None:
    """The Social Manager server listening on ``port``, if that's what holds it."""
    found = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True
    )
    for token in found.stdout.split():
        pid = int(token)
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        if "socialmgr" not in cmd.stdout:
            continue
        cwd = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True
        )
        folder = next((Path(line[1:]) for line in cwd.stdout.splitlines() if line.startswith("n")), None)
        return Holder(pid, folder)
    return None


def pause(holder: Holder, wait_s: float = 10.0) -> None:
    os.kill(holder.pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(holder.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    os.kill(holder.pid, signal.SIGKILL)


def resume(holder: Holder) -> bool:
    """Start the paused server again from its own install folder."""
    if holder.cwd is None or not (holder.cwd / ".venv" / "bin" / "socialmgr").exists():
        return False
    done = subprocess.run(
        [str(holder.cwd / ".venv" / "bin" / "socialmgr"), "launch", "--no-browser"],
        cwd=holder.cwd,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("SOCIALMGR_")},
    )
    return done.returncode == 0
