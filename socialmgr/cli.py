"""``socialmgr`` command line."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from socialmgr.core.config import Settings
from socialmgr.db import migrate
from socialmgr.db.session import make_engine, session_factory, session_scope

app = typer.Typer(
    help="Social Manager: local analytics and experiments for one X account.", no_args_is_help=True
)


def _settings() -> Settings:
    return Settings()


@app.command()
def init() -> None:
    """Create or upgrade the local database."""
    settings = _settings()
    migrate.upgrade(make_engine(settings))
    typer.echo(f"Database ready at {settings.db_path}")


@app.command()
def fixture(
    until: Annotated[
        str | None, typer.Option(help="Last run time, ISO UTC (default: today 06:00 UTC)")
    ] = None,
) -> None:
    """Load a deterministic fixture account by simulating daily runs over fake providers."""
    from socialmgr.services.fixtures import default_until, load_fixture
    from socialmgr.services.plugins import load_plugins

    settings = _settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    end = datetime.fromisoformat(until).astimezone(UTC) if until else default_until()
    with session_scope(session_factory(engine)) as session:
        summary = load_fixture(session, settings, load_plugins(settings), until=end)
    typer.echo(
        f"Fixture loaded: {summary.runs} runs, {summary.posts} posts, "
        f"{summary.forecasts} forecasts, {summary.scores} scored"
    )


@app.command()
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Serve the web app and API on localhost."""
    import uvicorn

    from socialmgr.api.app import create_app

    settings = _settings()
    migrate.upgrade(make_engine(settings))
    uvicorn.run(create_app(settings), host=host or settings.host, port=port or settings.port)


@app.command()
def backup() -> None:
    """Write a consistent copy of the database to the backup folder."""
    from socialmgr.services.backups import backup as do_backup

    typer.echo(str(do_backup(_settings())))


@app.command()
def restore(source: Path) -> None:
    """Replace the database with a backup (after an integrity check)."""
    from socialmgr.services.backups import restore as do_restore

    do_restore(_settings(), source)
    typer.echo(f"Restored from {source}")


@app.command()
def connect() -> None:
    """Connect your X account (OAuth 2.0 with PKCE in the browser) and store its tokens locally."""
    import httpx

    from socialmgr.providers.x.connect import run_connect
    from socialmgr.providers.x.errors import XError

    settings = _settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    try:
        with httpx.Client(timeout=30.0) as http:
            result = run_connect(settings, session_factory(engine), http=http, echo=typer.echo)
    except XError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1) from exc
    expires = result.expires_at.isoformat() if result.expires_at else "unknown"
    typer.echo(f"Connected @{result.username} (X user {result.user_id}). Access token valid until {expires}.")


@app.command()
def probe(
    follower_sample: Annotated[
        int, typer.Option(min=1, max=1000, help="Newest followers to sample for the order check")
    ] = 200,
) -> None:
    """Day-1 probes against the real X API: rate limits, follower order, analytics availability."""
    import httpx

    from socialmgr.providers.x.errors import XError
    from socialmgr.providers.x.probe import run_probe

    settings = _settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    try:
        with httpx.Client(timeout=30.0) as http:
            report, path = run_probe(
                settings, session_factory(engine), http=http, follower_sample=follower_sample
            )
    except XError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1) from exc
    printed = {**report, "follower_order": {**report["follower_order"], "sample_ids": "(saved to file)"}}
    typer.echo(json.dumps(printed, indent=2))
    typer.echo(f"Saved {path}")


@app.command()
def openapi(out: Path = Path("openapi.json")) -> None:
    """Write the API schema used to generate the web client's types."""
    from socialmgr.api.app import create_app

    out.write_text(json.dumps(create_app(_settings(), web_dist=None).openapi(), indent=2) + "\n")
    typer.echo(f"Wrote {out}")
