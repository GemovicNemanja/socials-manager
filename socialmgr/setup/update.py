"""Keep an install on the latest ``main``: fast-forward, reinstall, rebuild, or roll back.

The daily job calls this before it runs, so every member's install follows ``main`` (which only
moves after CI passes) without anyone doing anything. It never touches an install with local edits,
a checkout on another branch, or one whose history has diverged: it says so and leaves it alone.
A failure part-way returns the install to the version it had, so the day's run still happens.
"""

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

Tool = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class UpdateResult:
    status: str  # current | updated | skipped | failed
    old: str = ""
    new: str = ""
    detail: str = ""

    @property
    def summary(self) -> str:
        short = f"{self.old[:7]} → {self.new[:7]}" if self.old and self.new else ""
        return " ".join(p for p in (self.status, short, self.detail) if p)


def _tool(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, env=env, timeout=1800)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=300)


def _uv() -> str:
    return shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def _pnpm() -> list[str]:
    if shutil.which("pnpm"):
        return ["pnpm"]
    private = Path.home() / ".local" / "share" / "socialmgr" / "node" / "bin" / "corepack"
    corepack = shutil.which("corepack") or (str(private) if private.exists() else "corepack")
    return [corepack, "pnpm"]


class _Failed(RuntimeError):
    pass


def _check(done: subprocess.CompletedProcess[str], what: str) -> None:
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        raise _Failed(f"{what} failed: {' / '.join(tail) or f'exit {done.returncode}'}")


def _install(repo: Path, web: bool, tool: Tool) -> None:
    _check(tool([_uv(), "sync", "--locked", "--quiet"], repo), "uv sync")
    if web:
        _check(tool([*_pnpm(), "install", "--frozen-lockfile", "--silent"], repo / "web"), "pnpm install")
        _check(tool([*_pnpm(), "run", "--silent", "build"], repo / "web"), "web build")


def _web_changed(repo: Path, old: str, new: str) -> bool:
    diff = _git(repo, "diff", "--name-only", old, new, "--", "web", "openapi.json")
    return diff.returncode != 0 or bool(diff.stdout.strip())


def auto_update(repo: Path, tool: Tool = _tool, remote: str = "origin", branch: str = "main") -> UpdateResult:
    if not (repo / ".git").exists():
        return UpdateResult("skipped", detail="not a git checkout")
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if head != branch:
        return UpdateResult("skipped", detail=f"on branch {head!r}, not {branch!r}")
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return UpdateResult("skipped", detail="files in the install were edited; updates are paused")
    if _git(repo, "fetch", "--quiet", remote, branch).returncode != 0:
        return UpdateResult("skipped", detail="couldn't reach GitHub")
    old = _git(repo, "rev-parse", "HEAD").stdout.strip()
    new = _git(repo, "rev-parse", f"{remote}/{branch}").stdout.strip()
    if old == new:
        return UpdateResult("current", old, new)
    if _git(repo, "merge-base", "--is-ancestor", old, new).returncode != 0:
        return UpdateResult("skipped", old, new, "the install has commits main doesn't; updates are paused")
    web = _web_changed(repo, old, new)
    try:
        _check(_git(repo, "merge", "--ff-only", "--quiet", f"{remote}/{branch}"), "git merge")
        _install(repo, web, tool)
    except _Failed as exc:
        _git(repo, "reset", "--hard", "--quiet", old)  # tracked files only: data/ and .env are untracked
        try:
            _install(repo, web, tool)
        except _Failed as again:
            return UpdateResult("failed", old, new, f"{exc}; restoring the old version also failed: {again}")
        return UpdateResult("failed", old, new, f"{exc}; kept the previous version")
    return UpdateResult("updated", old, new)
