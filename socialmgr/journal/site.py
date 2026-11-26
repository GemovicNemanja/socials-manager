# ruff: noqa: E501  (long messages read better unbroken)
"""Build the public journal as a static site and publish it to a public GitHub repository.

The site is the prebuilt public app (``web/dist-journal``, built with the web app) plus one
``data.json`` snapshot built with ``preview=False``, so drafts never leave the machine. Publishing
commits the built files to the configured repository's branch (GitHub Pages serves it) and pushes;
it never force-pushes and never touches the app's own repository.
"""

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.journal import entries as je
from socialmgr.journal import snapshot

APP_ROOT = Path(__file__).resolve().parents[2]
WEB_JOURNAL = APP_ROOT / "web" / "dist-journal"


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildResult:
    out_dir: Path
    entries: int
    drafts_left_out: int


@dataclass(frozen=True)
class PublishResult:
    changed: bool
    commit: str | None
    url: str | None
    summary: str


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def _identity(settings: Settings) -> tuple[str, str]:
    """The author for site commits: config, else this app repository's own git identity."""
    cfg = settings.journal
    name = cfg.author_name or _git(APP_ROOT, "config", "user.name", check=False).stdout.strip()
    email = cfg.author_email or _git(APP_ROOT, "config", "user.email", check=False).stdout.strip()
    if not name or not email:
        raise PublishError(
            "No git identity for the site commits: set SOCIALMGR_JOURNAL__AUTHOR_NAME and "
            "SOCIALMGR_JOURNAL__AUTHOR_EMAIL in .env."
        )
    return name, email


def build(
    session: Session,
    settings: Settings,
    out_dir: Path,
    *,
    web_build: Path | None = None,
    now: datetime | None = None,
) -> BuildResult:
    """Write the public site into ``out_dir`` (replacing its files, keeping ``.git``)."""
    web_build = web_build or WEB_JOURNAL
    entry_html = web_build / "journal.html"
    if not entry_html.exists():
        entry_html = web_build / "index.html"
    if not entry_html.exists():
        raise PublishError(f"The public site isn't built ({web_build}): run `cd web && pnpm build` first.")
    snap = snapshot.build(session, settings, preview=False, now=now)
    out_dir.mkdir(parents=True, exist_ok=True)
    for child in out_dir.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for child in web_build.iterdir():
        if child.name in ("journal.html", "index.html"):
            continue
        dest = out_dir / child.name
        shutil.copytree(child, dest) if child.is_dir() else shutil.copy2(child, dest)
    title = f"{snap.site.handle} · {snap.site.tagline}"
    page = _set_meta(entry_html.read_text(encoding="utf-8"), title, snap.site.headline, snap.site.site_url)
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    (out_dir / "data.json").write_text(json.dumps(snap.model_dump(mode="json"), indent=1), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")  # serve files that start with "_"
    drafts = [e for e in je.load_all(settings.journal_dir) if not e.published]
    return BuildResult(out_dir=out_dir, entries=len(snap.entries), drafts_left_out=len(drafts))


def _set_meta(page: str, title: str, description: str, url: str | None) -> str:
    """Set the title and share tags, replacing the generic ones the page ships with."""
    tags = {
        ('name="description"'): description,
        ('property="og:title"'): title,
        ('property="og:description"'): description,
    }
    if url:
        tags['property="og:url"'] = url
    for attr, value in tags.items():
        tag = f'<meta {attr} content="{html.escape(value)}" />'
        found = re.search(rf"<meta\s+{re.escape(attr)}[^>]*>", page)
        page = (
            page[: found.start()] + tag + page[found.end() :]
            if found
            else page.replace("</head>", tag + "\n</head>", 1)
        )
    title_tag = f"<title>{html.escape(title)}</title>"
    found = re.search(r"<title>.*?</title>", page, flags=re.S)
    return (
        page[: found.start()] + title_tag + page[found.end() :]
        if found
        else page.replace("</head>", title_tag + "</head>", 1)
    )


def checkout_dir(settings: Settings) -> Path:
    return settings.journal_dir / "site-repo"


def _prepare_checkout(settings: Settings) -> Path:
    repo = settings.journal.repo
    if not repo:
        raise PublishError(
            "No public repository configured: set SOCIALMGR_JOURNAL__REPO (and SOCIALMGR_JOURNAL__SITE_URL) in .env."
        )
    path = checkout_dir(settings)
    branch = settings.journal.branch
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        _git(path, "init", "-q", "-b", branch)
        _git(path, "remote", "add", "origin", repo)
    else:
        _git(path, "remote", "set-url", "origin", repo)
    fetched = _git(path, "fetch", "-q", "origin", branch, check=False)
    if fetched.returncode == 0:
        # Follow the remote exactly (it only ever holds built files; nothing local to keep).
        _git(path, "checkout", "-q", "-B", branch, f"origin/{branch}")
    return path


def publish(session: Session, settings: Settings, now: datetime | None = None) -> PublishResult:
    """Build the site with published entries only and push it. Returns whether anything changed."""
    t = now or datetime.now(UTC)
    path = _prepare_checkout(settings)
    build(session, settings, path, now=t)
    _git(path, "add", "-A")
    if not _git(path, "status", "--porcelain").stdout.strip():
        return PublishResult(
            changed=False, commit=None, url=settings.journal.site_url, summary="No changes to publish."
        )
    name, email = _identity(settings)
    _git(
        path,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit",
        "-q",
        "-m",
        f"Update journal ({t:%Y-%m-%d %H:%M} UTC)",
    )
    pushed = _git(path, "push", "-q", "origin", f"HEAD:{settings.journal.branch}", check=False)
    if pushed.returncode != 0:
        raise PublishError(
            f"Push to {settings.journal.repo} failed: {pushed.stderr.strip() or pushed.stdout.strip()}"
        )
    commit = _git(path, "rev-parse", "--short", "HEAD").stdout.strip()
    where = settings.journal.site_url or settings.journal.repo
    return PublishResult(
        changed=True, commit=commit, url=settings.journal.site_url, summary=f"Published {commit} to {where}."
    )


def has_published_entry(settings: Settings) -> bool:
    return any(e.published for e in je.load_all(settings.journal_dir))
