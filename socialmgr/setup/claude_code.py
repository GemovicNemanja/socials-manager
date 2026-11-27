"""Claude Code: find it, check its sign-in, register the socialmgr MCP server for every folder."""

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from socialmgr.setup.names import LIVE, Names

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=60)


def find() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    for candidate in (home / ".local/bin/claude", home / ".claude/local/claude"):
        if candidate.exists():
            return str(candidate)
    return None


def signed_in(claude: str, run: Runner | None = None) -> bool:
    done = (run or _run)([claude, "auth", "status", "--json"])
    if done.returncode != 0:
        return False
    try:
        return bool(json.loads(done.stdout).get("loggedIn", True))
    except json.JSONDecodeError:
        return True


def mcp_command(repo: Path, uv: str) -> list[str]:
    """``uv run --directory`` so the server reads this install's ``.env`` from any working folder."""
    return [uv, "run", "--quiet", "--directory", str(repo), "socialmgr", "mcp"]


def register_mcp(claude: str, repo: Path, uv: str, run: Runner | None = None, names: Names = LIVE) -> None:
    """(Re)register at user scope, so every Claude Code session on this Mac sees the tools."""
    run = run or _run
    run([claude, "mcp", "remove", "--scope", "user", names.mcp_name])  # absent is fine
    done = run([claude, "mcp", "add", "--scope", "user", names.mcp_name, "--", *mcp_command(repo, uv)])
    if done.returncode != 0:
        raise RuntimeError(f"claude mcp add failed: {(done.stderr or done.stdout).strip()}")


def mcp_registered(claude: str, repo: Path, run: Runner | None = None, names: Names = LIVE) -> bool:
    """Registered, and pointing at this install (not an older checkout)."""
    done = (run or _run)([claude, "mcp", "get", names.mcp_name])
    return done.returncode == 0 and f"--directory {repo} socialmgr" in done.stdout


def unregister_mcp(claude: str, repo: Path, run: Runner | None = None, names: Names = LIVE) -> bool:
    """Remove the registration, but only if it points at this install."""
    if not mcp_registered(claude, repo, run, names):
        return False
    (run or _run)([claude, "mcp", "remove", "--scope", "user", names.mcp_name])
    return True
