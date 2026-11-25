"""Application services for agents (IMPLEMENTATION_PLAN.md §8). The MCP tools are thin wrappers.

Every response carries the same envelope: a templated ``summary``, a ``confidence`` label driven by
the status thresholds, ``as_of``, ``model_version``, ``regime_id``, ``data_sufficiency``,
``caveats`` and stable record ids (``post:…``, ``slot:…``, ``forecast:…``). Nothing here posts,
likes, replies or follows on X, edits a frozen forecast, or approves anything.
"""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Forecast, Label, PlannedSlot, Post, Run, ScoreRow
from socialmgr.labelling.taxonomy import FORMATS, LABEL_SET_VERSION, LLM_FIELDS
from socialmgr.modelling.regimes import Fetcher
from socialmgr.services import briefing, overview, pipeline, planned, post_detail, read_models, runs
from socialmgr.services.plugins import ActivePlugins

Confidence = Literal["solid", "suggestive", "too early"]
SUGGESTIVE_MIN = pipeline.OWNER_TERMS_MIN_POSTS  # 20 matured posts: owner-level terms
SOLID_MIN = 60  # MODEL §6 eligibility: owner deviations for type, format, link and video


class AgentResponse(BaseModel):
    summary: str
    confidence: Confidence
    as_of: datetime | None
    model_version: str | None
    regime_id: int | None
    data_sufficiency: read_models.DataSufficiency
    caveats: list[str] = Field(default_factory=list)


def _owner_n(session: Session, settings: Settings) -> int:
    rows = session.execute(
        select(ScoreRow.id)
        .join(Forecast, Forecast.id == ScoreRow.forecast_id)
        .where(
            ScoreRow.workspace_id == settings.workspace_id,
            ScoreRow.late_maturity.is_(False),
            Forecast.subject.like("post:%"),
        )
    ).all()
    return len(rows)


def confidence_for(owner_n: int, calibrated: bool = True) -> Confidence:
    """Driven by the status thresholds, and never above "too early" while forecasts use priors only."""
    if owner_n < SUGGESTIVE_MIN or not calibrated:
        return "too early"
    if owner_n < SOLID_MIN:
        return "suggestive"
    return "solid"


def _envelope(
    session: Session, settings: Settings, summary: str, caveats: list[str] | None = None
) -> dict[str, Any]:
    last = pipeline.last_finished_run(session, settings.workspace_id)
    owner_n = _owner_n(session, settings)
    latest = session.scalar(
        select(Forecast)
        .where(Forecast.workspace_id == settings.workspace_id)
        .order_by(Forecast.id.desc())
        .limit(1)
    )
    model_version = latest.model_version if latest else None
    calibrated = bool(latest and latest.input_manifest.get("calibrated"))
    notes = list(caveats or [])
    reason = None
    if not calibrated:
        reason = "prior_only_uncalibrated"
        notes.append(
            "Forecasts still come from priors only (owner-level terms need 20 matured posts and the M3 "
            f"model; {owner_n} matured so far): treat ranges as rough."
        )
    return {
        "summary": summary,
        "confidence": confidence_for(owner_n, calibrated),
        "as_of": last.cutoff_at if last else None,
        "model_version": model_version,
        "regime_id": pipeline.regime_at(
            session, settings.workspace_id, settings.account.platform, datetime.now(UTC), None
        ),
        "data_sufficiency": read_models.DataSufficiency(owner_n=owner_n, reason=reason),
        "caveats": notes,
    }


# ---------------------------------------------------------------- orientation


class BriefingResponse(AgentResponse):
    run_id: int | None
    data: dict[str, Any]


def get_briefing(session: Session, settings: Settings) -> BriefingResponse:
    b = briefing.latest(session, settings.workspace_id)
    if b is None:
        return BriefingResponse(
            **_envelope(session, settings, "No run has finished yet. Start one with start_run."),
            run_id=None,
            data={},
        )
    return BriefingResponse(**_envelope(session, settings, b.summary), run_id=b.run_id, data=b.data)


class StepStatus(BaseModel):
    name: str
    status: str
    error: str | None = None


class RunStatus(BaseModel):
    run_id: int
    status: str
    trigger: str
    started_at: datetime
    cutoff_at: datetime
    finished_at: datetime | None
    spend_usd: float
    steps: list[StepStatus]


class RunStatusResponse(AgentResponse):
    latest: RunStatus | None
    unfinished: RunStatus | None


def _run_status(run: Run) -> RunStatus:
    steps = run.steps or {}
    return RunStatus(
        run_id=run.id,
        status=run.status,
        trigger=run.trigger,
        started_at=run.started_at,
        cutoff_at=run.cutoff_at,
        finished_at=run.finished_at,
        spend_usd=round(run.spend_usd, 4),
        steps=[
            StepStatus(
                name=name,
                status=steps.get(name, {}).get("status", "pending"),
                error=steps.get(name, {}).get("error"),
            )
            for name in runs.STEPS.names
        ],
    )


def get_run_status(session: Session, settings: Settings) -> RunStatusResponse:
    latest = session.scalar(
        select(Run).where(Run.workspace_id == settings.workspace_id).order_by(Run.id.desc()).limit(1)
    )
    unfinished = runs.unfinished_run(session, settings.workspace_id)
    if latest is None:
        summary = "No runs yet."
    elif unfinished is not None:
        failed = [s for s in _run_status(unfinished).steps if s.status == "failed"]
        summary = (
            f"Run {unfinished.id} is unfinished"
            + (f" (stopped at {failed[0].name})" if failed else "")
            + "; start_run resumes it."
        )
    else:
        summary = f"Last run {latest.id} finished at {latest.finished_at:%Y-%m-%d %H:%M} UTC."
    return RunStatusResponse(
        **_envelope(session, settings, summary),
        latest=_run_status(latest) if latest else None,
        unfinished=_run_status(unfinished) if unfinished else None,
    )


def start_run(
    session: Session, settings: Settings, plugins: ActivePlugins, regime_fetcher: Fetcher | None = None
) -> BriefingResponse:
    """Run now (or resume an interrupted run) and return the new briefing."""
    try:
        runs.run_now(session, settings, plugins, trigger="agent", regime_fetcher=regime_fetcher)
    except runs.RunInProgress:
        return BriefingResponse(
            **_envelope(session, settings, "Another run is in progress; check get_run_status in a minute."),
            run_id=None,
            data={},
        )
    except runs.StepFailed as exc:
        return BriefingResponse(
            **_envelope(session, settings, str(exc), [exc.error]), run_id=exc.run_id, data={}
        )
    return get_briefing(session, settings)


# ---------------------------------------------------------------- posts and forecasts


class PostsResponse(AgentResponse):
    total: int
    counts_by_status: read_models.StatusCounts
    items: list[read_models.PostItem]


def get_posts(
    session: Session,
    settings: Settings,
    status: read_models.StatusFilter = "all",
    limit: int = 20,
    offset: int = 0,
) -> PostsResponse:
    page = read_models.list_posts(session, settings, status=status, limit=min(limit, 200), offset=offset)
    c = page.counts_by_status
    summary = (
        f"{page.total} posts with status {status}. Overall: {c.above} above range, "
        f"{c.as_expected} as expected, "
        f"{c.below} below, {c.provisional} under 48h."
    )
    return PostsResponse(
        **_envelope(session, settings, summary, page.caveats),
        total=page.total,
        counts_by_status=c,
        items=page.items,
    )


class PostResponse(AgentResponse):
    post: post_detail.PostDetail | None


def _post_id(ref: str) -> int:
    return int(ref.removeprefix("post:"))


def get_post(session: Session, settings: Settings, post_id: str) -> PostResponse:
    detail = post_detail.post_detail(session, settings, _post_id(post_id))
    if detail is None:
        return PostResponse(**_envelope(session, settings, f"No post {post_id}."), post=None)
    item = detail.post
    fq = detail.forecast.quantiles if detail.forecast else None
    q10, q50, q90 = ((fq.q10 or 0.0), (fq.q50 or 0.0), (fq.q90 or 0.0)) if fq else (0.0, 0.0, 0.0)
    if detail.score is not None and detail.forecast is not None:
        summary = (
            f"{item.id} reached ~{detail.score.outcome:,.0f} impressions at 48h; its frozen forecast said "
            f"{q10:,.0f}-{q90:,.0f} (80%), median {q50:,.0f}: {detail.score.label.replace('_', ' ')}."
        )
    elif detail.forecast is not None:
        summary = (
            f"{item.id} is {item.age_h:.0f}h old; expected 48h impressions {q10:,.0f}-{q90:,.0f} (80%), "
            f"median {q50:,.0f}."
        )
    else:
        summary = f"{item.id} has no forecast."
    return PostResponse(**_envelope(session, settings, summary, detail.caveats), post=detail)


class ForecastOut(BaseModel):
    id: str | None  # "forecast:<id>" when frozen; None for a draft
    subject: str
    kind: str
    frozen: bool
    quantiles: dict[str, float]
    model_version: str
    curve_version: str
    manifest_hash: str | None = None
    frozen_at: datetime | None = None


class ForecastResponse(AgentResponse):
    forecast: ForecastOut | None
    guardrails: list[planned.GuardrailHit] = Field(default_factory=list)


def _range_text(q: dict[str, float]) -> str:
    return f"median {q['q50']:,.0f}, 80% range {q['q10']:,.0f}-{q['q90']:,.0f} impressions at 48h"


def get_forecast(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    *,
    subject: str | None = None,
    draft: planned.PlanRequest | None = None,
) -> ForecastResponse:
    """A frozen forecast (``post:<id>`` or ``slot:<id>``), or a draft forecast that freezes nothing."""
    if subject:
        fc = session.scalar(
            select(Forecast)
            .where(Forecast.workspace_id == settings.workspace_id, Forecast.subject == subject)
            .order_by(Forecast.id)
            .limit(1)
        )
        if fc is None:
            return ForecastResponse(
                **_envelope(session, settings, f"No forecast for {subject}."), forecast=None
            )
        q = {k: round(float(v), 1) for k, v in fc.quantiles.items()}
        return ForecastResponse(
            **_envelope(session, settings, f"Frozen {fc.kind} forecast for {subject}: {_range_text(q)}."),
            forecast=ForecastOut(
                id=f"forecast:{fc.id}",
                subject=subject,
                kind=fc.kind,
                frozen=True,
                quantiles=q,
                model_version=fc.model_version,
                curve_version=fc.curve_version,
                manifest_hash=fc.manifest_hash,
                frozen_at=fc.frozen_at,
            ),
        )
    if draft is None:
        raise ValueError("give a subject (post:<id> or slot:<id>) or a draft")
    d = planned.draft_forecast(session, settings, plugins, draft, datetime.now(UTC))
    q = {k: round(v, 1) for k, v in d.quantiles.items()}
    return ForecastResponse(
        **_envelope(
            session,
            settings,
            f"Draft forecast (not frozen): {_range_text(q)}. "
            "Register it with register_planned_post to freeze it.",
        ),
        forecast=ForecastOut(
            id=None,
            subject="draft",
            kind="draft",
            frozen=False,
            quantiles=q,
            model_version=d.model_version,
            curve_version=d.curve_version,
        ),
        guardrails=d.guardrails,
    )


class PlannedResponse(AgentResponse):
    slot: str | None
    forecast: ForecastOut | None
    guardrails: list[planned.GuardrailHit] = Field(default_factory=list)
    registered: bool


def register_planned_post(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    req: planned.PlanRequest,
    accept_warnings: bool,
) -> PlannedResponse:
    try:
        result = planned.register_planned_post(
            session, settings, plugins, req, now=datetime.now(UTC), allow_warnings=accept_warnings
        )
    except ValueError as exc:
        if not isinstance(exc, planned.PlanRefused):
            return PlannedResponse(
                **_envelope(session, settings, f"Not registered: {exc}."),
                slot=None,
                forecast=None,
                registered=False,
            )
        blocks = [h for h in exc.hits if h.severity == "block"]
        hint = "" if blocks else " Call again with accept_warnings=true to register anyway."
        return PlannedResponse(
            **_envelope(session, settings, f"Not registered: {exc}.{hint}"),
            slot=None,
            forecast=None,
            guardrails=exc.hits,
            registered=False,
        )
    session.commit()
    fc = result.forecast
    q = {k: round(float(v), 1) for k, v in fc.quantiles.items()}
    return PlannedResponse(
        **_envelope(
            session,
            settings,
            f"Registered slot:{result.slot.id} for {result.slot.planned_at:%Y-%m-%d %H:%M} UTC; "
            f"its forecast is frozen: {_range_text(q)}. "
            "Post within 30 minutes of the planned time (or with this text) to match.",
        ),
        slot=f"slot:{result.slot.id}",
        forecast=ForecastOut(
            id=f"forecast:{fc.id}",
            subject=fc.subject,
            kind=fc.kind,
            frozen=True,
            quantiles=q,
            model_version=fc.model_version,
            curve_version=fc.curve_version,
            manifest_hash=fc.manifest_hash,
            frozen_at=fc.frozen_at,
        ),
        guardrails=result.guardrails,
        registered=True,
    )


class SlotOut(BaseModel):
    slot: str
    planned_at: datetime
    state: str
    post_type: str
    text: str | None
    matched_post: str | None
    forecast_q10: float | None
    forecast_q50: float | None
    forecast_q90: float | None


class ScheduleResponse(AgentResponse):
    slots: list[SlotOut]


def get_schedule(session: Session, settings: Settings, include_past_days: int = 7) -> ScheduleResponse:
    since = datetime.now(UTC).replace(microsecond=0) - timedelta(days=include_past_days)
    slots = session.scalars(
        select(PlannedSlot)
        .where(PlannedSlot.workspace_id == settings.workspace_id, PlannedSlot.planned_at >= since)
        .order_by(PlannedSlot.planned_at)
    ).all()
    out = []
    for s in slots:
        fc = session.scalar(select(Forecast).where(Forecast.subject == f"slot:{s.id}").limit(1))
        q = fc.quantiles if fc else {}
        out.append(
            SlotOut(
                slot=f"slot:{s.id}",
                planned_at=s.planned_at,
                state=s.state,
                post_type=s.post_type,
                text=s.text,
                matched_post=f"post:{s.matched_post_id}" if s.matched_post_id else None,
                forecast_q10=round(q["q10"], 1) if q else None,
                forecast_q50=round(q["q50"], 1) if q else None,
                forecast_q90=round(q["q90"], 1) if q else None,
            )
        )
    upcoming = sum(1 for s in out if s.state == "planned")
    return ScheduleResponse(
        **_envelope(
            session, settings, f"{upcoming} planned posts upcoming; {len(out) - upcoming} recent ones."
        ),
        slots=out,
    )


class LabelResponse(AgentResponse):
    post: str
    field: str
    value: str
    saved: bool


LABEL_CHOICES: dict[str, tuple[str, ...]] = {"format": FORMATS, **LLM_FIELDS}


def set_label(session: Session, settings: Settings, post_id: str, field: str, value: str) -> LabelResponse:
    """Record the owner's label for a post; owner labels take precedence over rules and the LLM.
    Frozen forecasts keep the labels they were made with."""
    choices = LABEL_CHOICES.get(field)
    post = session.get(Post, _post_id(post_id))
    if post is None or choices is None or value not in choices:
        problem = (
            f"No post {post_id}."
            if post is None
            else f"Unknown field {field!r}; use one of {sorted(LABEL_CHOICES)}."
            if choices is None
            else f"{value!r} isn't a {field}; use one of {list(choices)}."
        )
        return LabelResponse(
            **_envelope(session, settings, problem), post=post_id, field=field, value=value, saved=False
        )
    session.add(
        Label(
            workspace_id=settings.workspace_id,
            post_id=post.id,
            label_set_version=LABEL_SET_VERSION,
            field=field,
            value=value,
            source="owner",
            confidence=None,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    return LabelResponse(
        **_envelope(
            session,
            settings,
            f"Set {field}={value} on post:{post.id} (owner label; frozen forecasts unchanged).",
        ),
        post=f"post:{post.id}",
        field=field,
        value=value,
        saved=True,
    )


# ---------------------------------------------------------------- account and data


class OverviewResponse(AgentResponse):
    overview: overview.Overview


def get_account_overview(session: Session, settings: Settings) -> OverviewResponse:
    ov = overview.overview(session, settings)
    t = ov.tiles
    parts = [f"{t.followers.now:,} followers" if t.followers.now is not None else "followers unknown"]
    if t.followers.change_7d is not None:
        parts.append(f"{t.followers.change_7d:+,} in 7 days")
    parts.append(
        f"{ov.status_7d.above} above / {ov.status_7d.as_expected} as expected / "
        f"{ov.status_7d.below} below range in 7 days"
    )
    return OverviewResponse(**_envelope(session, settings, "; ".join(parts) + ".", ov.caveats), overview=ov)


class ExportResponse(AgentResponse):
    files: list[str]


EXPORT_TABLES = ("posts", "forecasts", "scores")


def export_data(
    session: Session,
    settings: Settings,
    what: Literal["posts", "forecasts", "scores", "all"],
    fmt: Literal["csv", "json"],
) -> ExportResponse:
    """Write exports to ``<data_dir>/exports`` and return their paths (contents stay on disk)."""
    out_dir = settings.data_dir.resolve() / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tables = EXPORT_TABLES if what == "all" else (what,)
    files: list[str] = []
    for table in tables:
        rows = _export_rows(session, settings, table)
        path = out_dir / f"{table}-{stamp}.{fmt}"
        _write(path, rows, fmt)
        files.append(str(path))
    return ExportResponse(
        **_envelope(session, settings, f"Exported {', '.join(tables)} to {out_dir}."), files=files
    )


def _export_rows(session: Session, settings: Settings, table: str) -> list[dict[str, Any]]:
    if table == "posts":
        page = read_models.list_posts(session, settings, limit=1_000_000)
        return [
            {
                "id": p.id,
                "external_id": p.external_id,
                "created_at": p.created_at.isoformat(),
                "type": p.type,
                "format": p.format,
                "topic": p.topic,
                "text": p.text,
                "i48": p.i48.value,
                "i48_provisional": p.i48.provisional,
                "status": p.status,
                "pit_mid": p.pit_mid,
            }
            for p in page.items
        ]
    if table == "forecasts":
        return [
            {
                "id": f"forecast:{f.id}",
                "subject": f.subject,
                "kind": f.kind,
                "model_version": f.model_version,
                "curve_version": f.curve_version,
                "frozen_at": f.frozen_at.isoformat(),
                "manifest_hash": f.manifest_hash,
                **{k: round(float(v), 3) for k, v in f.quantiles.items()},
            }
            for f in session.scalars(select(Forecast).where(Forecast.workspace_id == settings.workspace_id))
        ]
    return [
        {
            "forecast": f"forecast:{s.forecast_id}",
            "outcome_i48": s.outcome,
            "capture_age_h": s.capture_age_h,
            "late_maturity": s.late_maturity,
            "pit_mid": s.pit_mid,
            "crps_log": s.crps_log,
            "in_50": s.in_50,
            "in_80": s.in_80,
            "scored_at": s.scored_at.isoformat(),
        }
        for s in session.scalars(select(ScoreRow).where(ScoreRow.workspace_id == settings.workspace_id))
    ]


def _write(path: Path, rows: list[dict[str, Any]], fmt: str) -> None:
    if fmt == "json":
        path.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        return
    with path.open("w", newline="") as fh:
        if not rows:
            return
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
