"""MCP server: the member's own agent reads and plans through the same services as the web app.

Run with ``socialmgr mcp`` (stdio). Add it to Claude Code with:

    claude mcp add socialmgr -- uv run --directory /path/to/socials-manager socialmgr mcp

No tool can post, like, reply or follow on X, edit a frozen forecast, or approve anything
(IMPLEMENTATION_PLAN.md §8).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from sqlalchemy.orm import Session

from socialmgr import __version__
from socialmgr.core.config import Settings
from socialmgr.core.types import PostType
from socialmgr.db import migrate
from socialmgr.db.session import make_engine, session_factory
from socialmgr.modelling.regimes import Fetcher, github_fetcher
from socialmgr.services import agent, briefing, planned, read_models
from socialmgr.services.plugins import ActivePlugins, load_plugins

INSTRUCTIONS = """Social Manager tracks the member's own X account: every post gets a forecast frozen before
it was published (an 80% range of 48h impressions), and is scored against it once it is 48 hours old.
Start with get_briefing. Use get_posts / get_post for history, get_forecast for a draft, and
register_planned_post to freeze a forecast before the member posts. Respect `confidence`: "too early"
means forecasts still come from priors only. Quote record ids (post:…, slot:…, forecast:…). You cannot
post, like, reply or follow on X, and frozen forecasts can't be changed."""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE_LOCAL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def create_server(
    settings: Settings | None = None,
    plugins: ActivePlugins | None = None,
    regime_fetcher: Fetcher | Literal["default"] | None = "default",
) -> MCPServer:
    settings = settings or Settings()
    engine = make_engine(settings)
    migrate.upgrade(engine)
    factory = session_factory(engine)
    active = plugins or load_plugins(settings)
    fetcher: Fetcher | None = github_fetcher() if regime_fetcher == "default" else regime_fetcher

    @contextmanager
    def session() -> Iterator[Session]:
        with factory() as s:
            yield s

    server = MCPServer("socialmgr", title="Social Manager", instructions=INSTRUCTIONS, version=__version__)

    def tool(annotations: ToolAnnotations) -> Callable[[Callable[..., object]], Callable[..., object]]:
        return server.tool(annotations=annotations)

    # ------------------------------------------------------------ orientation

    @tool(READ)
    def get_briefing() -> agent.BriefingResponse:
        """The latest run's briefing: new posts, which matured posts landed above/below their frozen
        range, spend against the monthly cap, and anything that needs attention."""
        with session() as s:
            return agent.get_briefing(s, settings)

    @tool(WRITE_LOCAL)
    def start_run() -> agent.BriefingResponse:
        """Collect from X now (or resume an interrupted run), label, freeze forecasts, score matured
        posts, and return the new briefing. Takes up to a minute; costs about $0.05-0.20 of X credit.
        Reads already made today aren't billed again."""
        with session() as s:
            return agent.start_run(s, settings, active, fetcher)

    @tool(READ)
    def get_run_status() -> agent.RunStatusResponse:
        """The latest run and any unfinished run, step by step."""
        with session() as s:
            return agent.get_run_status(s, settings)

    # ------------------------------------------------------------ posts and forecasts

    @tool(READ)
    def get_posts(
        status: read_models.StatusFilter = "all", limit: int = 20, offset: int = 0
    ) -> agent.PostsResponse:
        """The member's posts, newest first, each with its 48h impressions (estimated while under 48h),
        frozen forecast range and status (above / as_expected / below / provisional)."""
        with session() as s:
            return agent.get_posts(s, settings, status=status, limit=limit, offset=offset)

    @tool(READ)
    def get_post(post_id: str) -> agent.PostResponse:
        """One post in full: labels, frozen forecast (quantiles, model and curve version, manifest
        hash), every capture by age, and its score. ``post_id`` is "post:<n>"."""
        with session() as s:
            return agent.get_post(s, settings, post_id)

    @tool(READ)
    def get_forecast(
        subject: str | None = None,
        planned_at: datetime | None = None,
        text: str | None = None,
        post_type: PostType = "original",
        post_format: str | None = None,
        topic: str | None = None,
    ) -> agent.ForecastResponse:
        """A frozen forecast for ``subject`` ("post:<n>" or "slot:<n>"), or, with ``planned_at``
        (ISO time with offset), a draft forecast for a post the member might write. Drafts freeze
        nothing; they also report guardrail warnings (near-duplicates, posts within 3h)."""
        with session() as s:
            if subject:
                return agent.get_forecast(s, settings, active, subject=subject)
            if planned_at is None:
                raise ValueError("give subject, or planned_at for a draft")
            labels = {k: v for k, v in (("format", post_format), ("topic", topic)) if v}
            req = planned.PlanRequest(
                planned_at=planned_at, text=text, post_type=post_type, labels=labels, registered_by="agent"
            )
            return agent.get_forecast(s, settings, active, draft=req)

    @tool(WRITE_LOCAL)
    def register_planned_post(
        planned_at: datetime,
        text: str | None = None,
        post_type: PostType = "original",
        post_format: str | None = None,
        topic: str | None = None,
        accept_warnings: bool = False,
    ) -> agent.PlannedResponse:
        """Register a post the member will publish at ``planned_at`` (ISO time with offset, in the
        future). Its forecast is frozen and hashed now, before the post exists, then matched to the
        real post at a later run (same text, or within 30 minutes of the planned time) and scored.
        Near-duplicates of earlier posts are refused; spacing warnings need accept_warnings=true."""
        with session() as s:
            labels = {k: v for k, v in (("format", post_format), ("topic", topic)) if v}
            req = planned.PlanRequest(
                planned_at=planned_at, text=text, post_type=post_type, labels=labels, registered_by="agent"
            )
            return agent.register_planned_post(s, settings, active, req, accept_warnings)

    @tool(READ)
    def get_schedule(include_past_days: int = 7) -> agent.ScheduleResponse:
        """Planned posts: upcoming ones with their frozen ranges, and recent ones (posted or missed)."""
        with session() as s:
            return agent.get_schedule(s, settings, include_past_days)

    @tool(WRITE_LOCAL)
    def set_label(post_id: str, field: str, value: str) -> agent.LabelResponse:
        """Correct a post's label as the member (format, topic, content_kind or hook_type). Owner
        labels win over rules and the LLM for future use; frozen forecasts keep their labels."""
        with session() as s:
            return agent.set_label(s, settings, post_id, field, value)

    # ------------------------------------------------------------ account and data

    @tool(READ)
    def get_account_overview() -> agent.OverviewResponse:
        """Followers and their trend, 7-day impressions against the expected range, post status
        counts, forecast hit rates, last run and spend."""
        with session() as s:
            return agent.get_account_overview(s, settings)

    @tool(WRITE_LOCAL)
    def export_data(
        what: Literal["posts", "forecasts", "scores", "all"] = "all", fmt: Literal["csv", "json"] = "csv"
    ) -> agent.ExportResponse:
        """Write posts, forecasts and/or scores to files in the local data folder; returns the paths."""
        with session() as s:
            return agent.export_data(s, settings, what, fmt)

    # ------------------------------------------------------------ resources and prompts

    @server.resource("socialmgr://briefing/latest", mime_type="text/plain")
    def latest_briefing() -> str:
        """The latest run briefing as text."""
        with session() as s:
            b = briefing.latest(s, settings.workspace_id)
            return b.summary if b else "No run has finished yet."

    @server.prompt(name="weekly_review", title="Weekly review")
    def weekly_review() -> str:
        """Review the last week of posts against their forecasts."""
        return (
            "Call get_briefing and get_posts (limit 30). Summarise last week for me: which posts landed "
            "above or below their frozen range and what they have in common (format, topic, timing), "
            "what's still under 48h, and spend. Respect the confidence label; don't over-read a few posts."
        )

    @server.prompt(name="plan_next_week", title="Plan next week's posts")
    def plan_next_week() -> str:
        """Plan next week's posts and freeze their forecasts."""
        return (
            "Look at get_posts and get_schedule, then help me plan next week's posts. For each idea, call "
            "get_forecast with a draft to see its expected range and guardrails; when I confirm, call "
            "register_planned_post so the forecast is frozen before I post. Keep originals 3h+ apart."
        )

    return server


def main() -> None:
    create_server().run("stdio")
