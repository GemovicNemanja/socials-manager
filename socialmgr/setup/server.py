"""Start, find and stop the local web server in the background (for the Social Manager app)."""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
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
