"""CLI commands for setup, the app launcher and the daily-run schedule."""

import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from socialmgr.core.config import Settings
from socialmgr.setup import macos, server, wizard


def register(app: typer.Typer) -> None:
    @app.command()
    def setup(
        handle: Annotated[str | None, typer.Option(help="Your X handle, e.g. @your_name")] = None,
        tz: Annotated[str | None, typer.Option(help="IANA time zone (default: this Mac's)")] = None,
        premium: Annotated[bool | None, typer.Option("--premium/--no-premium", help="X Premium")] = None,
        client_id: Annotated[str | None, typer.Option(help="Your X app's OAuth 2.0 Client ID")] = None,
        backup_dir: Annotated[str | None, typer.Option(help="Folder for backups")] = None,
        run_at: Annotated[str | None, typer.Option(help="Daily run time, 24h local, e.g. 09:00")] = None,
        connect: Annotated[bool, typer.Option(help="Connect X in the browser")] = True,
        mcp: Annotated[bool, typer.Option(help="Register the MCP server with Claude Code")] = True,
        mac_app: Annotated[bool, typer.Option("--app/--no-app", help="Write the Social Manager app")] = True,
        schedule: Annotated[bool, typer.Option(help="Install the daily run")] = True,
        first_run: Annotated[bool, typer.Option(help="Offer the first collection at the end")] = True,
        reconfigure: Annotated[bool, typer.Option(help="Ask every question again")] = False,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Never prompt; given or kept values")] = False,
        uv: Annotated[str | None, typer.Option(hidden=True)] = None,
        repo: Annotated[Path, typer.Option(hidden=True)] = wizard.APP_ROOT,
        applications: Annotated[Path | None, typer.Option(hidden=True)] = None,
        launch_agents: Annotated[Path | None, typer.Option(hidden=True)] = None,
        load_agent: Annotated[bool, typer.Option(hidden=True)] = True,
    ) -> None:
        """Set this Mac up (re-runnable): settings, X connection, Claude Code + MCP, app, daily run."""
        opts = wizard.Options(
            repo=repo.resolve(),
            uv=uv,
            handle=handle,
            tz=tz,
            premium=premium,
            client_id=client_id,
            backup_dir=backup_dir,
            run_at=run_at,
            connect=connect,
            mcp=mcp,
            app=mac_app,
            schedule=schedule,
            first_run=first_run,
            reconfigure=reconfigure,
            yes=yes,
            applications=applications,
            launch_agents=launch_agents,
            load_agent=load_agent,
        )
        try:
            raise typer.Exit(wizard.run_setup(opts))
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from exc

    @app.command()
    def launch(
        no_browser: Annotated[bool, typer.Option("--no-browser", help="Start only")] = False,
    ) -> None:
        """Open the dashboard, starting the server in the background if it isn't running."""
        settings = Settings()
        opener = (lambda _u: None) if no_browser else webbrowser.open
        try:
            typer.echo(server.launch(settings, opener))
        except RuntimeError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    @app.command()
    def stop() -> None:
        """Stop the background server the Social Manager app started."""
        typer.echo("Stopped." if server.stop(Settings()) else "No background server was running.")

    @app.command("schedule")
    def schedule_cmd(
        at: Annotated[str | None, typer.Option(help="Daily run time, 24h local, e.g. 09:00")] = None,
        off: Annotated[bool, typer.Option("--off", help="Remove the daily run")] = False,
    ) -> None:
        """Show, set or remove the daily automatic run (a launchd job on this Mac)."""
        if off:
            typer.echo("Daily run removed." if macos.unschedule() else "No daily run was scheduled.")
            return
        if at is None:
            current = macos.scheduled()
            typer.echo(
                f"Daily run at {current.hour:02d}:{current.minute:02d}; log: data/logs/daily-run.log"
                if current
                else "No daily run scheduled. Set one with: socialmgr schedule --at 09:00"
            )
            return
        try:
            when = wizard.parse_time(at)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        macos.schedule(wizard.APP_ROOT, when)
        typer.echo(f"Daily run at {when.hour:02d}:{when.minute:02d} (runs on wake if the Mac was asleep).")
