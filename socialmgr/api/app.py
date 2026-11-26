"""One process serves the REST API and the built web app (IMPLEMENTATION_PLAN.md §3)."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from socialmgr import __version__
from socialmgr.api import health_schemas, schemas
from socialmgr.core.config import Settings
from socialmgr.db.session import make_engine, session_factory
from socialmgr.journal import snapshot as journal_snapshot
from socialmgr.journal.schema import JournalSnapshot
from socialmgr.modelling.regimes import Fetcher, github_fetcher
from socialmgr.services import agent, experiment_views, follower_views, model_views, post_detail, read_models
from socialmgr.services import overview as overview_model
from socialmgr.services.plugins import ActivePlugins, load_plugins

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
WEB_JOURNAL_DIST = WEB_DIST.parent / "dist-journal"


class _RunTracker:
    """Counts runs this process is executing, so status can tell a live run from an interrupted one."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0

    def __enter__(self) -> None:
        with self._lock:
            self._n += 1

    def __exit__(self, *_: object) -> None:
        with self._lock:
            self._n -= 1

    @property
    def active(self) -> bool:
        return self._n > 0


def create_app(
    settings: Settings | None = None,
    web_dist: Path | None = WEB_DIST,
    journal_dist: Path | None = WEB_JOURNAL_DIST,
    plugins: ActivePlugins | None = None,
    regime_fetcher: Fetcher | Literal["default"] | None = "default",
) -> FastAPI:
    settings = settings or Settings()
    factory = session_factory(make_engine(settings))
    loaded: list[ActivePlugins] = [plugins] if plugins is not None else []
    plugins_lock = threading.Lock()
    tracker = _RunTracker()
    app = FastAPI(
        title="Social Manager", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    def active_plugins() -> ActivePlugins:
        """Loaded on first use, so serving reads and writing the schema never build providers."""
        with plugins_lock:
            if not loaded:
                loaded.append(load_plugins(settings))
            return loaded[0]

    def fetcher() -> Fetcher | None:
        if regime_fetcher == "default":
            return github_fetcher() if settings.run.regime_check else None
        return regime_fetcher

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/shell", tags=["shell"])
    def shell(session: SessionDep) -> read_models.Shell:
        return read_models.shell(session, settings)

    @app.get("/api/posts", tags=["posts"])
    def posts(
        session: SessionDep,
        status: read_models.StatusFilter = "all",
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> read_models.PostsPage:
        return read_models.list_posts(session, settings, status=status, limit=limit, offset=offset)

    @app.get("/api/posts/{post_id}", tags=["posts"], responses={404: {"description": "No such post"}})
    def post(session: SessionDep, post_id: str) -> post_detail.PostDetail:
        """One post: labels, frozen forecast, score and captures. Accepts `post:12` or `12`."""
        raw = post_id.removeprefix("post:")
        detail = post_detail.post_detail(session, settings, int(raw)) if raw.isdigit() else None
        if detail is None:
            raise HTTPException(status_code=404, detail=f"post {post_id} not found")
        return detail

    @app.get("/api/overview", tags=["overview"])
    def overview(session: SessionDep) -> overview_model.Overview:
        return overview_model.overview(session, settings)

    @app.get("/api/briefing", tags=["runs"])
    def briefing(session: SessionDep) -> schemas.BriefingOut:
        """The latest run's briefing: summary lines, notable posts with record ids, caveats, spend."""
        return schemas.briefing_out(agent.get_briefing(session, settings))

    @app.get("/api/runs/status", tags=["runs"])
    def run_status(session: SessionDep) -> schemas.RunStatusOut:
        """The latest run and any unfinished run, step by step, with a classified failure if any."""
        return schemas.run_status_out(session, agent.get_run_status(session, settings), tracker.active)

    @app.post("/api/runs", tags=["runs"])
    def start_run(session: SessionDep) -> schemas.RunStartOut:
        """Run now, or resume an interrupted run, and return the new briefing.

        Blocks until the run ends (seconds to a minute). It runs in the server's thread pool, so it
        finishes even if the browser goes away; poll ``GET /api/runs/status`` meanwhile. If another
        run holds the lock, returns at once and the status shows that run.
        """
        with tracker:
            result = agent.start_run(session, settings, active_plugins(), fetcher())
        status = agent.get_run_status(session, settings)
        return schemas.RunStartOut(
            briefing=schemas.briefing_out(result),
            status=schemas.run_status_out(session, status, tracker.active),
        )

    @app.get("/api/planned", tags=["planned"])
    def schedule(
        session: SessionDep, include_past_days: Annotated[int, Query(ge=0, le=90)] = 7
    ) -> agent.ScheduleResponse:
        """Planned posts: upcoming ones with their frozen ranges, and recent ones (posted or missed)."""
        return agent.get_schedule(session, settings, include_past_days=include_past_days)

    @app.post(
        "/api/planned/draft",
        tags=["planned"],
        responses={409: {"description": "No owner account yet: run once first"}},
    )
    def draft(session: SessionDep, body: schemas.PlanIn) -> agent.ForecastResponse:
        """The forecast a planned post would get, with guardrail warnings. Freezes nothing."""
        try:
            return agent.get_forecast(session, settings, active_plugins(), draft=body.to_request())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/planned", tags=["planned"])
    def register(session: SessionDep, body: schemas.RegisterIn) -> agent.PlannedResponse:
        """Register a planned post and freeze its forecast now, before the post exists."""
        return agent.register_planned_post(
            session, settings, active_plugins(), body.to_request(), body.accept_warnings
        )

    # ------------------------------------------------------------ experiments (M2), as the owner

    @app.get("/api/experiments", tags=["experiments"])
    def experiments(session: SessionDep) -> schemas.ExperimentsOut:
        """Running, waiting and concluded experiments, E01-E09 with power previews, pending approvals."""
        return experiment_views.experiments_overview(session, settings)

    @app.post("/api/experiments", tags=["experiments"])
    def propose(session: SessionDep, body: schemas.ProposeIn) -> schemas.ExperimentActionOut:
        """Start a suggestion or a custom experiment as the owner: scheduled directly, or a pending
        approval for MODEL §6.9's owner items (horizons over 8 weeks, a 4th concurrent experiment)."""
        return experiment_views.propose(session, settings, active_plugins(), body)

    @app.post("/api/experiments/slots", tags=["experiments"])
    def register_slots(session: SessionDep, body: schemas.RegisterSlotsIn) -> schemas.RegisterSlotsOut:
        """Register whole local days (up to 7 ahead) for every scheduled or active experiment.
        Each slot's treatment-blind forecast is frozen before its arms are drawn."""
        return experiment_views.register_slots(session, settings, active_plugins(), body.days)

    @app.get(
        "/api/experiments/{key}", tags=["experiments"], responses={404: {"description": "No such experiment"}}
    )
    def experiment(session: SessionDep, key: str) -> schemas.ExperimentDetailOut:
        """Protocol, slots with arms and blind ranges, readouts, guardrails, deviations, e-LOND."""
        detail = experiment_views.experiment_detail(session, settings, key)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"experiment {key} not found")
        return detail

    @app.post("/api/experiments/{key}/slots", tags=["experiments"])
    def register_slots_for(
        session: SessionDep, key: str, body: schemas.RegisterSlotsIn
    ) -> schemas.RegisterSlotsOut:
        """Register whole days for this experiment only (other running experiments draw nothing
        on these days; use ``POST /api/experiments/slots`` to run them as a factorial)."""
        return experiment_views.register_slots(session, settings, active_plugins(), body.days, keys=[key])

    @app.post("/api/experiments/{key}/stop", tags=["experiments"])
    def stop(session: SessionDep, key: str, body: schemas.StopIn) -> schemas.ExperimentActionOut:
        """Stop for good: planned treated slots revert to control and no e-value is emitted."""
        return experiment_views.stop(session, settings, key, body.reason)

    @app.post("/api/experiments/{key}/extend", tags=["experiments"])
    def extend(session: SessionDep, key: str) -> schemas.ExperimentActionOut:
        """MODEL §6.3's one extension of up to 50% (past 8 weeks it becomes a pending approval)."""
        return experiment_views.extend(session, settings, key)

    @app.get("/api/approvals", tags=["experiments"])
    def approvals(
        session: SessionDep, status: Literal["pending", "approved", "rejected", "all"] = "pending"
    ) -> list[schemas.ApprovalCardOut]:
        return experiment_views.approvals_list(session, settings, None if status == "all" else status)

    def _decide(
        session: Session, approval_id: int, decision: str, body: schemas.DecideIn | None
    ) -> schemas.DecideOut:
        out = experiment_views.decide(
            session, settings, active_plugins(), approval_id, decision, body.note if body else None
        )
        if out.approval is None:
            raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
        return out

    @app.post(
        "/api/approvals/{approval_id}/approve",
        tags=["experiments"],
        responses={404: {"description": "No such approval"}},
    )
    def approve(
        session: SessionDep, approval_id: int, body: schemas.DecideIn | None = None
    ) -> schemas.DecideOut:
        """The owner approves a pending item. No MCP tool can reach this."""
        return _decide(session, approval_id, "approve", body)

    @app.post(
        "/api/approvals/{approval_id}/reject",
        tags=["experiments"],
        responses={404: {"description": "No such approval"}},
    )
    def reject(
        session: SessionDep, approval_id: int, body: schemas.DecideIn | None = None
    ) -> schemas.DecideOut:
        return _decide(session, approval_id, "reject", body)

    @app.get("/api/next-posts", tags=["experiments"])
    def next_posts(session: SessionDep, days: Annotated[int, Query(ge=1, le=14)] = 7) -> schemas.NextPostsOut:
        """Registered slots and planned posts for the next days, with arm instructions and frozen ranges."""
        return experiment_views.next_posts(session, settings, days)

    # ------------------------------------------------------------ model health, followers, goals (M3)

    @app.get("/api/model", tags=["model"])
    def model(session: SessionDep) -> health_schemas.ModelHealthOut:
        """The Forecasts screen: model card, release gates, calibration, drift monitors, changes."""
        return model_views.model_health(session, settings)

    @app.post("/api/alarms/{alarm_id}/ack", tags=["model"])
    def ack_alarm(
        session: SessionDep, alarm_id: int, body: health_schemas.AckIn | None = None
    ) -> health_schemas.AckOut:
        """Acknowledge a detector alarm. Nothing else changes."""
        return model_views.acknowledge(
            session, settings, alarm_id, body.note if body else None, "owner", datetime.now(UTC)
        )

    @app.get("/api/followers", tags=["followers"])
    def followers(session: SessionDep) -> health_schemas.FollowersOut:
        """The Account screen's follower panels: tiles, follower forecast band, funnel, goal, past."""
        return follower_views.followers_view(session, settings)

    @app.get("/api/goals", tags=["followers"])
    def goals_list(session: SessionDep) -> health_schemas.GoalsOut:
        """Current goals with their status, and every goal ever set."""
        return follower_views.goals_overview(session, settings)

    @app.post("/api/goals", tags=["followers"])
    def set_goal(session: SessionDep, body: health_schemas.GoalIn) -> health_schemas.GoalSetOut:
        """Set a goal: supersedes the scope's current goal and starts a new period (logged)."""
        return follower_views.set_goal(session, settings, body, "web", datetime.now(UTC))

    @app.post("/api/goals/preview", tags=["followers"])
    def preview_goal(session: SessionDep, body: health_schemas.GoalIn) -> health_schemas.GoalPreviewOut:
        """Where you'd stand today against a goal you haven't saved: P(goal met), median path."""
        return follower_views.goal_preview(session, settings, active_plugins(), body, datetime.now(UTC))

    # ------------------------------------------------------------ public journal (M4)

    @app.get("/api/journal", tags=["journal"])
    def journal(session: SessionDep, preview: bool = True) -> JournalSnapshot:
        """The public journal's data. ``preview`` (the default here) includes draft entries, marked;
        the published site is built with ``preview=false`` and never contains drafts."""
        return journal_snapshot.build(session, settings, preview=preview)

    # The public site's build, for the owner's preview at /journal/ (it reads /api/journal?preview=true
    # because the build holds no data.json). Mounted before the dashboard's catch-all.
    if journal_dist is not None and (journal_dist / "index.html").exists():

        @app.get("/journal", include_in_schema=False)
        def journal_slash() -> RedirectResponse:
            return RedirectResponse("/journal/")  # relative asset and data URLs need the slash

        app.mount("/journal", StaticFiles(directory=journal_dist, html=True), name="journal")

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")
        index = web_dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str, request: Request) -> FileResponse:
            candidate = (web_dist / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(web_dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(index)

    return app
