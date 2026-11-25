"""``socialmgr`` command line."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session, sessionmaker

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
    experiment_demo: Annotated[
        bool,
        typer.Option(
            "--experiment-demo",
            help="Test fixture: also run an E03 x E06 experiment over the last 3 weeks (end-to-end tests)",
        ),
    ] = False,
) -> None:
    """Load a deterministic fixture account by simulating daily runs over fake providers."""
    from sqlalchemy import select

    from socialmgr.db.models import Run
    from socialmgr.services.fixtures import default_until, load_experiment_demo, load_fixture
    from socialmgr.services.plugins import load_plugins

    settings = _settings()
    # Fixtures always use the fake providers, whatever .env selects: never the real X account.
    fake = tuple(p for p in settings.plugins.providers if p.startswith("fake")) or ("fake_owner",)
    settings = settings.model_copy(
        update={"plugins": settings.plugins.model_copy(update={"providers": fake})}
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    with session_factory(engine)() as session:
        if session.scalar(select(Run.id).where(Run.trigger != "fixture").limit(1)) is not None:
            typer.echo(
                f"{settings.db_path} holds real runs; refusing to add fake data to it. "
                "Point SOCIALMGR_DATA_DIR at another folder for the fixture.",
                err=True,
            )
            raise typer.Exit(1)
    end = datetime.fromisoformat(until).astimezone(UTC) if until else default_until()
    with session_scope(session_factory(engine)) as session:
        load = load_experiment_demo if experiment_demo else load_fixture
        summary = load(session, settings, load_plugins(settings), until=end)
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


@app.command()
def run() -> None:
    """Collect, label, forecast, score and write the briefing (resumes an interrupted run)."""
    from socialmgr.modelling.regimes import github_fetcher
    from socialmgr.services import briefing, runs
    from socialmgr.services.plugins import load_plugins

    settings = _settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    with session_factory(engine)() as session:
        try:
            outcomes = runs.run_now(
                session, settings, load_plugins(settings), regime_fetcher=github_fetcher()
            )
        except (runs.RunInProgress, runs.StepFailed) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        for outcome in outcomes:
            if outcome.resumed:
                typer.echo(f"Resumed run {outcome.run_id} (cutoff {outcome.cutoff_at:%Y-%m-%d %H:%M} UTC).")
        latest = briefing.latest(session, settings.workspace_id)
        if latest is not None:
            typer.echo(latest.summary)


@app.command()
def plan(
    at: Annotated[str, typer.Option(help="When you'll post, ISO time (local times need an offset)")],
    text: Annotated[
        str | None, typer.Option(help="The draft text (enables duplicate checks and labels)")
    ] = None,
    post_type: Annotated[
        str, typer.Option("--type", help="original | quote | reply | self_reply")
    ] = "original",
    force: Annotated[bool, typer.Option(help="Register despite spacing warnings")] = False,
) -> None:
    """Register a planned post: its forecast is frozen now, before the post exists."""
    from datetime import UTC, datetime

    from socialmgr.services import planned
    from socialmgr.services.plugins import load_plugins

    settings = _settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    planned_at = datetime.fromisoformat(at)
    if planned_at.tzinfo is None:
        from zoneinfo import ZoneInfo

        planned_at = planned_at.replace(tzinfo=ZoneInfo(settings.account.tz))
    req = planned.PlanRequest(
        planned_at=planned_at.astimezone(UTC),
        text=text,
        post_type=post_type,
        registered_by="cli",
    )
    with session_scope(session_factory(engine)) as session:
        try:
            result = planned.register_planned_post(
                session, settings, load_plugins(settings), req, now=datetime.now(UTC), allow_warnings=force
            )
        except planned.PlanRefused as exc:
            for hit in exc.hits:
                typer.echo(f"{hit.severity}: {hit.detail}", err=True)
            typer.echo(
                "Not registered. Use --force to accept warnings (blocks can't be overridden).", err=True
            )
            raise typer.Exit(1) from exc
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        q = result.forecast.quantiles
        typer.echo(
            f"Registered slot:{result.slot.id} for {result.slot.planned_at:%Y-%m-%d %H:%M} UTC. "
            f"Expected 48h impressions: median {q['q50']:,.0f}, 80% range {q['q10']:,.0f} to {q['q90']:,.0f} "
            f"({result.forecast.kind}, forecast:{result.forecast.id}, frozen)."
        )
        for hit in result.guardrails:
            typer.echo(f"{hit.severity}: {hit.detail}")


@app.command()
def mcp() -> None:
    """Serve the agent tools over MCP (stdio). Add to Claude Code with:
    claude mcp add socialmgr -- uv run --directory <this folder> socialmgr mcp"""
    from socialmgr.agent_server import main

    main()


# ---------------------------------------------------------------- experiments (M2)

experiments_app = typer.Typer(
    help="Experiments (MODEL §6). With no subcommand: list them.", invoke_without_command=True
)
app.add_typer(experiments_app, name="experiments")


def _db() -> tuple[Settings, sessionmaker[Session]]:
    settings = _settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    return settings, session_factory(engine)


@experiments_app.callback()
def experiments(ctx: typer.Context) -> None:
    """List experiments and pending approvals."""
    if ctx.invoked_subcommand is not None:
        return
    from socialmgr.services import experiments as svc

    settings, factory = _db()
    with factory() as session:
        r = svc.get_experiments(session, settings, include_suggestions=False)
        if not r.experiments:
            typer.echo("No experiments yet. See `socialmgr experiments suggest`.")
        for e in r.experiments:
            nxt = f", next look {e.next_look.look} after {e.next_look.after:%Y-%m-%d}" if e.next_look else ""
            factors = ", ".join(f"{f['name']} ({f['control']} vs {f['treatment']})" for f in e.factors)
            typer.echo(f"{e.key:8} {e.status:16} {e.name}: {factors}; {e.horizon_weeks:g} weeks{nxt}")
            if e.outcome:
                typer.echo(f"         {e.outcome}")
        for a in r.pending_approvals:
            typer.echo(f"Approval {a.approval_id} ({a.kind}, {a.experiment}): {'; '.join(a.reasons)}")


@experiments_app.command("suggest")
def experiments_suggest() -> None:
    """E01-E09 with a power preview for this account's posting rate."""
    from socialmgr.services import experiments as svc

    settings, factory = _db()
    with factory() as session:
        for s in svc.get_experiments(session, settings).suggestions:
            power = ", ".join(f"{k} {'n/a' if v is None else f'{v:.2f}'}" for k, v in s.power.power.items())
            running = " [running]" if s.already_running else ""
            typer.echo(f"{s.key} {s.name}{running}\n    {s.design}, {s.horizon_text}; power {power}")


@experiments_app.command("propose")
def experiments_propose(
    key: Annotated[str, typer.Argument(help="A suggestion key, E01-E09")],
    start: Annotated[bool, typer.Option(help="Schedule it now (or ask for approval)")] = True,
) -> None:
    """Propose an experiment from a suggestion (as the owner)."""
    from socialmgr.experiments import engine as exp_engine
    from socialmgr.services.plugins import load_plugins

    settings, factory = _db()
    with session_scope(factory) as session:
        try:
            r = exp_engine.propose_experiment(
                session,
                settings,
                load_plugins(settings),
                actor="owner",
                now=datetime.now(UTC),
                suggestion_key=key,
                start=start,
            )
        except exp_engine.ExperimentError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"{r.experiment.key} is {r.experiment.status} (seed {r.experiment.seed}).")
        if r.approval is not None:
            typer.echo(f"Needs approval {r.approval.id}: {'; '.join(r.reasons)}.")


@experiments_app.command("slots")
def experiments_slots(
    times: Annotated[str, typer.Option(help="Local slot times, e.g. 07:00,11:00,16:00")],
    days: Annotated[int, typer.Option(min=1, max=7, help="Whole days from tomorrow")] = 1,
    label: Annotated[list[str] | None, typer.Option(help="Slot label field=value (repeatable)")] = None,
    treatment_times: Annotated[
        str | None, typer.Option(help="Switchbacks: the treatment condition's slot times")
    ] = None,
) -> None:
    """Register whole local days of experiment slots: blind forecasts first, then the draw."""
    from datetime import time, timedelta

    from socialmgr.experiments import engine as exp_engine
    from socialmgr.experiments.specs import DayPlan, SlotSpec
    from socialmgr.services import experiments as svc
    from socialmgr.services.plugins import load_plugins

    settings, factory = _db()
    labels = dict(item.split("=", 1) for item in (label or []))
    control = [time.fromisoformat(t.strip()) for t in times.split(",") if t.strip()]
    treat = [time.fromisoformat(t.strip()) for t in treatment_times.split(",")] if treatment_times else None
    with session_scope(factory) as session:
        tz = exp_engine.account_tz(session, settings)
        today = datetime.now(UTC).astimezone(tz).date()
        plans = [
            DayPlan(
                day=today + timedelta(days=i),
                slots=[SlotSpec(time=t, labels=labels) for t in control],
                treatment_slots=[SlotSpec(time=t, labels=labels) for t in treat] if treat else None,
            )
            for i in range(1, days + 1)
        ]
        try:
            r = exp_engine.register_slots(
                session, settings, load_plugins(settings), plans, now=datetime.now(UTC), actor="owner"
            )
        except exp_engine.ExperimentError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        for rs in r.slots:
            out = svc.slot_out(session, settings, rs.slot.id, rs.cancelled)
            typer.echo(
                f"{out.slot} {out.planned_local}: {out.instructions} (blind median {out.blind_q50:,.0f})"
            )
        for w in r.warnings:
            typer.echo(f"note: {w}")


@experiments_app.command("show")
def experiments_show(key: str) -> None:
    """An experiment's status, latest readout and upcoming slots."""
    from socialmgr.services import experiments as svc

    settings, factory = _db()
    with factory() as session:
        r = svc.get_experiment(session, settings, key)
        typer.echo(r.summary)
        if r.readouts:
            last = r.readouts[-1]
            for factor, text in last.labels.items():
                f = last.factors[factor]
                ratio, p = f.get("effect_ratio"), f.get("randomization_p")
                extra = f", effect {ratio:.2f}x, randomization p {p}" if ratio else ""
                typer.echo(f"  {factor}: {text}{extra}")
        for s in r.upcoming_slots:
            typer.echo(f"  {s.slot} {s.planned_local} [{s.state}]: {s.instructions}")
        for c in r.caveats:
            typer.echo(f"note: {c}")


@experiments_app.command("stop")
def experiments_stop(
    key: str, reason: Annotated[str, typer.Option(help="Why")] = "stopped by the owner"
) -> None:
    """Stop an experiment for good (no e-value)."""
    from socialmgr.experiments import engine as exp_engine

    settings, factory = _db()
    with session_scope(factory) as session:
        try:
            exp = exp_engine.stop_experiment(
                session, settings, key, actor="owner", reason=reason, now=datetime.now(UTC)
            )
        except exp_engine.ExperimentError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"{exp.key} stopped.")


@app.command()
def approvals() -> None:
    """List approvals waiting for you (MODEL §6.9)."""
    from socialmgr.services import experiments as svc

    settings, factory = _db()
    with factory() as session:
        rows = svc.list_approvals(session, settings).approvals
        if not rows:
            typer.echo("No pending approvals.")
        for a in rows:
            typer.echo(
                f"{a.approval_id}: {a.kind} for {a.experiment or '-'} requested by {a.requested_by} "
                f"at {a.requested_at:%Y-%m-%d %H:%M} UTC: {'; '.join(a.reasons)}\n    {json.dumps(a.payload)}"
            )


def _decide(approval_id: int, decision: str, note: str | None) -> None:
    from socialmgr.experiments.engine import ExperimentError
    from socialmgr.services import approvals as owner
    from socialmgr.services.plugins import load_plugins

    settings, factory = _db()
    with factory() as session:
        try:
            out = owner.decide(
                session, settings, approval_id, decision, note=note, plugins=load_plugins(settings)
            )
        except ExperimentError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Approval {out.approval_id} ({out.kind}, {out.experiment}) {out.status}.")


@app.command()
def approve(
    approval_id: int, note: Annotated[str | None, typer.Option(help="A note kept with the decision")] = None
) -> None:
    """Approve a pending item as the owner (agents can't)."""
    _decide(approval_id, "approve", note)


@app.command()
def reject(
    approval_id: int, note: Annotated[str | None, typer.Option(help="A note kept with the decision")] = None
) -> None:
    """Reject a pending item as the owner."""
    _decide(approval_id, "reject", note)
