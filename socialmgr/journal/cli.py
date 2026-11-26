"""`socialmgr journal …`: draft weekly entries, preview the public site, publish it."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.db import migrate
from socialmgr.db.session import make_engine, session_factory
from socialmgr.journal import drafts, site
from socialmgr.journal import entries as je

journal_app = typer.Typer(
    help="The public journal: draft weekly entries, preview the site, publish to GitHub Pages.",
    no_args_is_help=True,
)


def _open() -> tuple[Settings, sessionmaker[Session]]:
    settings = Settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return settings, session_factory(engine)


@journal_app.command("status")
def status() -> None:
    """Entries (draft or published) and where the site is published."""
    settings = Settings()
    rows = je.load_all(settings.journal_dir)
    if not rows:
        typer.echo("No entries yet. Start with: socialmgr journal draft --week 0")
    for e in rows:
        state = f"published {e.published_at:%Y-%m-%d}" if e.published and e.published_at else "draft"
        typer.echo(f"week {e.week:>2}  {state:<20} {e.title}  ({e.path})")
    cfg = settings.journal
    typer.echo(f"\nPublic repo: {cfg.repo or 'not set (SOCIALMGR_JOURNAL__REPO)'}")
    typer.echo(f"Site URL:    {cfg.site_url or 'not set (SOCIALMGR_JOURNAL__SITE_URL)'}")
    typer.echo(
        "Preview:     http://127.0.0.1:8765/journal/ while `socialmgr serve` runs (drafts shown, marked)"
    )


@journal_app.command("draft")
def draft(
    week: Annotated[
        int | None, typer.Option(help="Week number (0 = the first week). Default: the last full week.")
    ] = None,
) -> None:
    """Write a draft entry from the week's data, with TODOs where only you know the story."""
    settings, factory = _open()
    with factory() as session:
        try:
            entry = drafts.draft_week(session, settings, week)
        except je.EntryError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
    typer.echo(f"Draft written: {entry.path}")
    typer.echo("Edit it, remove every TODO, then: socialmgr journal publish --week " + str(entry.week))


@journal_app.command("build")
def build(
    out: Annotated[Path, typer.Option(help="Folder to write the static site into.")] = Path(
        "data/journal/site"
    ),
) -> None:
    """Build the public site locally (published entries only) without pushing it anywhere."""
    settings, factory = _open()
    with factory() as session:
        try:
            result = site.build(session, settings, out)
        except site.PublishError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
    typer.echo(
        f"Built {result.out_dir} with {result.entries} published entr{'y' if result.entries == 1 else 'ies'}"
        + (f"; {result.drafts_left_out} draft(s) left out." if result.drafts_left_out else ".")
    )


@journal_app.command("publish")
def publish(
    week: Annotated[int | None, typer.Option(help="Also mark this week's entry published first.")] = None,
) -> None:
    """Push the site to the public repository. With --week, that entry becomes public too."""
    settings, factory = _open()
    now = datetime.now(UTC)
    try:
        if week is not None:
            entry = je.publish(settings.journal_dir, week, now)
            typer.echo(f"Week {entry.week} marked published: {entry.title}")
        with factory() as session:
            result = site.publish(session, settings, now)
    except (je.EntryError, site.PublishError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.summary)
