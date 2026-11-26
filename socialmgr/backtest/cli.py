"""`socialmgr backtest …`: find candidate accounts, pull their public posts, run the external backtest."""

import json
from datetime import UTC, datetime
from typing import Annotated

import typer

from socialmgr.backtest import runner
from socialmgr.backtest.xpublic import BudgetExceeded, PublicReader
from socialmgr.core.config import Settings
from socialmgr.db import migrate
from socialmgr.db.session import make_engine, session_factory

backtest_app = typer.Typer(
    help="External backtest: the models on other accounts' public posts (paid X reads, cached).",
    no_args_is_help=True,
)


def _reader(budget: float) -> tuple[Settings, PublicReader]:
    settings = Settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    session = session_factory(engine)()
    return settings, PublicReader(session, settings, budget_usd=budget)


def _row(u: dict[str, object]) -> str:
    m = u.get("public_metrics") or {}
    assert isinstance(m, dict)
    followers = int(m.get("followers_count") or 0)
    return (
        f"@{u.get('username'):<20} {followers:>9,} followers  "
        f"{int(m.get('tweet_count') or 0):>7,} posts  {runner.band_of(followers):<9}  "
        f"{str(u.get('description') or '').replace(chr(10), ' ')[:70]}"
    )


@backtest_app.command("lookup")
def lookup(
    handles: Annotated[list[str], typer.Argument(help="Handles to check, e.g. @levelsio")],
    budget: Annotated[float, typer.Option(help="Most this command may spend, USD.")] = 1.0,
) -> None:
    """Follower counts and size bands ($0.01 per account; cached)."""
    _, reader = _reader(budget)
    try:
        users = reader.users(handles)
    except BudgetExceeded as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for u in sorted(users, key=lambda u: (u.get("public_metrics") or {}).get("followers_count", 0)):
        typer.echo(_row(u))
    typer.echo(f"\nSpent ${reader.spent:.2f}.")


@backtest_app.command("search")
def search(
    query: Annotated[
        str, typer.Argument(help="X search query, e.g. '\"building in public\" -is:retweet lang:en'")
    ],
    max_results: Annotated[int, typer.Option(help="Posts to read (10-100).")] = 25,
    budget: Annotated[float, typer.Option(help="Most this command may spend, USD.")] = 1.0,
) -> None:
    """Authors of recent posts matching a query, with follower counts (to find smaller accounts)."""
    _, reader = _reader(budget)
    try:
        users = reader.search_authors(query, max_results)
    except BudgetExceeded as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for u in sorted(users, key=lambda u: (u.get("public_metrics") or {}).get("followers_count", 0)):
        typer.echo(_row(u))
    typer.echo(f"\nSpent ${reader.spent:.2f}.")


@backtest_app.command("run")
def run(
    handles: Annotated[list[str], typer.Argument(help="The accounts to test, one per size band.")],
    posts: Annotated[int, typer.Option(help="Recent posts per account (reposts excluded).")] = 200,
    budget: Annotated[float, typer.Option(help="Most this command may spend, USD.")] = 5.0,
) -> None:
    """Pull each account's recent posts ($0.005 each, cached) and write the backtest report."""
    settings, reader = _reader(budget)
    now = datetime.now(UTC)
    results = []
    try:
        users = {u["username"].lower(): u for u in reader.users(handles)}
        for h in handles:
            user = users.get(h.lstrip("@").lower())
            if user is None:
                typer.echo(f"@{h.lstrip('@')}: not found, skipped", err=True)
                continue
            tweets = reader.timeline(str(user["id"]), posts)
            typer.echo(f"@{user['username']}: {len(tweets)} posts read; backtesting…")
            results.append(runner.run_account(user["username"], user, tweets, now))
    except BudgetExceeded as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    out_dir = settings.data_dir / "backtest"
    text = runner.report(results, now)
    path = out_dir / f"report-{now:%Y%m%d-%H%M}.md"
    path.write_text(text, encoding="utf-8")
    (out_dir / f"results-{now:%Y%m%d-%H%M}.json").write_text(
        json.dumps([r.__dict__ for r in results], default=str, indent=1), encoding="utf-8"
    )
    typer.echo(text)
    typer.echo(f"Report: {path}\nSpent ${reader.spent:.2f} (cached reads are free).")
