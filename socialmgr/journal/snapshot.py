# ruff: noqa: E501  (public prose strings read better unbroken)
"""Build the public journal snapshot from the local database and the entry files."""

import math
import statistics
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Experiment, FollowerForecast, ProtocolVersion, Run
from socialmgr.experiments import instructions
from socialmgr.experiments import monitor as exp_monitor
from socialmgr.experiments.suggestions import BY_KEY
from socialmgr.journal import entries as je
from socialmgr.journal import schema as j
from socialmgr.services import experiment_views, model_views, pipeline, read_models
from socialmgr.services.follower_data import follower_points, run_intervals
from socialmgr.services.model_data import scored_posts

PUBLIC_STATUSES = ("scheduled", "active", "paused", "concluded", "stopped")
TOP_POSTS = 5
ALL = 1_000_000


def _handle(settings: Settings, session: Session) -> str:
    owner = pipeline.owner_account(session, settings.workspace_id)
    handle = owner.handle if owner is not None else settings.account.handle
    return handle if handle.startswith("@") else f"@{handle}"


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=UTC)


def _count_at(points: list[tuple[int, datetime, int, int | None]], t: datetime) -> int | None:
    before = [p for p in points if p[1] <= t]
    return before[-1][2] if before else (points[0][2] if points else None)


def _result(session: Session, settings: Settings, exp: Experiment) -> j.ExperimentResult:
    if exp.status == "scheduled":
        return "scheduled"
    if exp.status in ("active", "paused"):
        return "running"
    if exp.status == "stopped":
        return "stopped"
    decisions = {str(d.get("decision")) for d in (exp.decisions or {}).values()} if exp.decisions else set()
    return "effect_found" if "adopt" in decisions else "no_effect"


def _arm_label(value: str) -> str:
    return value.replace("_", " ").strip().capitalize()


def _day(d: datetime) -> str:
    return f"{d:%b} {d.day}"


def public_experiment(
    session: Session, settings: Settings, exp: Experiment, now: datetime
) -> j.PublicExperiment:
    detail = experiment_views.experiment_detail(session, settings, exp.key, now=now)
    assert detail is not None
    summary = detail.experiment
    assert summary is not None
    readout = detail.readout
    factor = readout.factors[0] if readout and readout.factors else None
    first = exp.factors[0] if exp.factors else {}
    control_label = _arm_label(str(first.get("control", "control")))
    treatment_label = _arm_label(str(first.get("treatment", "treatment")))
    name = str(first.get("name", ""))
    points: list[j.ArmPoint] = []
    ratios: dict[str, list[float]] = {control_label: [], treatment_label: []}
    for o in exp_monitor.slot_outcomes(session, exp):
        if o.y is None or o.f <= 0 or name not in o.arms:
            continue
        label = treatment_label if o.arms[name] else control_label
        ratio = (o.y + 1) / (o.f + 1)
        points.append(j.ArmPoint(arm=label, ratio=round(ratio, 4)))
        ratios[label].append(ratio)
    arms = [
        j.ArmSummary(
            label=lbl,
            control=lbl == control_label,
            n=len(vals),
            typical_ratio=round(statistics.median(vals), 3) if vals else None,
        )
        for lbl, vals in ratios.items()
    ]
    notes = je.experiment_notes(settings.journal_dir, exp.key)
    # The page title: the owner's question if they wrote one, else the hypothesis under test.
    question = notes.question or exp.hypothesis.strip().rstrip(".")
    suggestion = BY_KEY.get(exp.suggestion_key or "")
    registered = session.scalar(
        select(ProtocolVersion.created_at)
        .where(ProtocolVersion.experiment_id == exp.id)
        .order_by(ProtocolVersion.version)
        .limit(1)
    )
    result = _result(session, settings, exp)
    effect = factor.effect_ratio if factor else None
    ci = tuple(factor.interval80) if factor and factor.interval80 else None
    p_better = None
    if factor and factor.p_lt_zero is not None:
        p_better = round(1 - factor.p_lt_zero, 3)
    answer: str | None
    if notes.answer:
        answer = notes.answer
    elif result in ("running", "scheduled"):
        answer = "Too early to say: the experiment is still collecting posts."
    elif effect is not None and ci is not None:
        answer = (
            f"Posts with {treatment_label} reached {effect:.2f}x of their forecast relative to "
            f"{control_label} (80% interval {ci[0]:.2f}-{ci[1]:.2f})."
        )
    else:
        answer = exp.outcome
    design = instructions.design_label(exp.design)
    progress = (
        f"{summary.posts_matured} / {summary.slots_assigned}"
        if exp.design == "per_post"
        else f"week {summary.week or 0} / {summary.weeks_total}"
    )
    setup = [
        j.MethodFact(label="Question", value=question),
        j.MethodFact(label="Change", value=f"{treatment_label} vs {control_label}"),
        j.MethodFact(
            label="Which posts", value=summary.factors[0].applies_to if summary.factors else "every post"
        ),
        j.MethodFact(
            label="Who decided",
            value="A coin flip per post, balanced within each day"
            if exp.design == "per_post"
            else "A coin flip per 4-day block",
        ),
        j.MethodFact(
            label="Measured", value=f"{summary.primary_metric_label}, against each post's frozen forecast"
        ),
        j.MethodFact(label="Stop rule", value=f"{exp.horizon_weeks:g} weeks, or a clear harm earlier"),
        j.MethodFact(
            label="Safety check", value="Pause an arm if it does clearly worse than 0.8x its forecast"
        ),
    ]
    if registered is not None:
        setup.append(
            j.MethodFact(label="Plan registered", value=f"{_day(registered)}, before any post was assigned")
        )
    return j.PublicExperiment(
        key=exp.key,
        slug=exp.key.lower(),
        name=exp.name,
        question=question,
        hypothesis=exp.hypothesis,
        answer=answer,
        design_label=design,
        metric_label=summary.primary_metric_label,
        result=result,
        posts_matured=summary.posts_matured,
        posts_planned=summary.slots_assigned,
        progress_label=progress,
        effect_ratio=effect,
        effect_ci80=(float(ci[0]), float(ci[1])) if ci else None,
        p_better=p_better,
        confirmed=bool(factor and factor.confirmed),
        randomization_p=factor.randomization_p if factor else None,
        started_at=exp.started_at,
        concluded_at=exp.concluded_at,
        registered_at=registered,
        arms=arms,
        points=points,
        why_markdown=notes.why or (suggestion.mechanism if suggestion else None),
        changed_markdown=notes.changed if result not in ("running", "scheduled") else None,
        setup=setup,
        note_slug=None,
        related_keys=[],
    )


def _post_row(item: read_models.PostItem, handle: str) -> j.PublicPostRow:
    fc = item.forecast
    text = " ".join(item.text.split())
    return j.PublicPostRow(
        text=text if len(text) <= 90 else text[:89] + "…",
        url=f"https://x.com/{handle.lstrip('@')}/status/{item.external_id}",
        posted_at=item.created_at,
        format=item.format,
        topic=item.topic,
        i2h=None,  # daily batch runs rarely capture a post near 2h; shown only when one exists
        i48=item.i48.value,
        likes_per_1k=item.likes_per_1k,
        q10=fc.q10 if fc else None,
        q50=fc.q50 if fc else None,
        q90=fc.q90 if fc else None,
        outcome="provisional" if item.status in ("provisional", "no_forecast") else item.status,
    )


def entry_stats(
    session: Session,
    settings: Settings,
    start: date,
    end: date,
    posts: list[read_models.PostItem],
) -> j.EntryStats:
    owner = pipeline.owner_account(session, settings.workspace_id)
    t0, t1 = _day_start(start), _day_start(end + timedelta(days=1))
    delta = None
    expected = None
    impressions = None
    change = None
    if owner is not None:
        pts = follower_points(session, owner.id)
        a, b = _count_at(pts, t0), _count_at(pts, t1)
        delta = b - a if a is not None and b is not None else None
        fc = session.scalar(
            select(FollowerForecast)
            .where(
                FollowerForecast.account_id == owner.id,
                FollowerForecast.horizon_days == 7,
                FollowerForecast.start_at <= t0 + timedelta(days=1),
            )
            .order_by(FollowerForecast.start_at.desc())
            .limit(1)
        )
        if fc is not None and abs((fc.start_at - t0).days) <= 2:
            q = fc.quantiles
            expected = (
                round(float(q["q10"]) - fc.start_followers),
                round(float(q["q90"]) - fc.start_followers),
            )
        ivs = run_intervals(session, owner.id)
        this = [i.impressions for i in ivs if t0 < i.end_at <= t1]
        prev = [i.impressions for i in ivs if t0 - timedelta(days=7) < i.end_at <= t0]
        impressions = float(sum(this)) if this else None
        if impressions is not None and prev and sum(prev) > 0:
            change = impressions / sum(prev) - 1
    scored = [p for p in posts if p.status in ("above", "as_expected", "below")]
    in80 = [
        p
        for p in scored
        if p.forecast is not None
        and p.i48.value is not None
        and p.forecast.q10 <= p.i48.value <= p.forecast.q90
    ]
    return j.EntryStats(
        followers_delta=delta,
        followers_expected=expected,
        impressions=impressions,
        impressions_change=change,
        posts_scored=len(scored),
        posts_in_80=len(in80),
        hit_80=len(in80) / len(scored) if scored else None,
    )


def method(session: Session, settings: Settings) -> j.PublicMethod:
    m = model_views.model_health(session, settings)
    cov = {c.level: c.value for c in m.coverage}
    stage = m.stage
    calib = (
        f"Berkowitz p = {m.calibration.berkowitz_p:.2f}, {'passes' if m.calibration.passed else 'fails'}"
        if m.calibration.berkowitz_p is not None
        else "From 10 matured posts"
    )
    skill = (
        f"{'Yes' if m.skill.passed else 'Not yet'}, {m.skill.value:+.2f} CRPS skill on the log scale"
        if m.skill.value is not None
        else f"Checked at the first model review ({m.first_review_at} matured posts)"
    )
    drift = next((x for x in m.monitors if x.chart == "d1_location"), None)
    drift_text = (
        "Off until 20 matured posts"
        if drift is None or not drift.armed
        else ("Alarm: forecasts have drifted" if drift.state == "alarm" else "Quiet")
    )
    return j.PublicMethod(
        headline="How I make the numbers on this site, and how far to trust them",
        intro=(
            "I write down a forecast for each post before it goes out, and I never edit it. That one rule is "
            "what makes the hit rate, the post status and every experiment here checkable."
        ),
        sections=[
            j.MethodSection(
                number=1,
                title="What I measure",
                body=(
                    "X shows a new post to people outside my followers mostly in its first two days, so my main "
                    "outcome is impressions 48 hours after posting. I collect once a day, so each post's 48-hour "
                    "number is read off its captures with a fitted maturity curve."
                ),
                facts=[
                    j.MethodFact(label="Main outcome", value="Impressions 48 hours after posting"),
                    j.MethodFact(label="Captures", value="Once a day, every post under 30 days old"),
                    j.MethodFact(label="Source", value="My own X analytics, official API"),
                    j.MethodFact(label="Followers", value="Daily count, plus new followers and unfollows"),
                ],
                coverage=[],
            ),
            j.MethodSection(
                number=2,
                title="How forecasts work",
                body=(
                    "My account started with no history, so the first forecasts come from published starting "
                    "assumptions. Once enough of my own posts have matured, a model fitted on them is backtested "
                    "against a simple baseline, and it replaces the old one only if it passes every check on "
                    "posts it never saw."
                ),
                facts=[
                    j.MethodFact(
                        label="Model", value="Negative binomial count model with in- and out-of-network reach"
                    ),
                    j.MethodFact(label="Inputs", value="Post type, link, followers at posting, time"),
                    j.MethodFact(label="Output", value="A range: 10th, 50th and 90th percentile"),
                    j.MethodFact(label="Frozen", value="Before posting, with a hash of every input"),
                    j.MethodFact(
                        label="Stage",
                        value="Starting assumptions only"
                        if stage == "prior_only"
                        else "Fitted on my own posts",
                    ),
                ],
                coverage=[],
            ),
            j.MethodSection(
                number=3,
                title="Are the forecasts honest?",
                body=(
                    "If the ranges are right, 80% of my posts should land inside their 80% range, not 95% (too "
                    f"cautious) or 60% (overconfident). These are my last {m.hit_80.n} matured posts."
                ),
                facts=[
                    j.MethodFact(label="Calibration test", value=calib),
                    j.MethodFact(label="Beats a simple baseline", value=skill),
                    j.MethodFact(label="Drift alarm", value=drift_text),
                ],
                coverage=[
                    j.CoverageRow(label="Inside the 50% range (target 50%)", target=0.5, value=cov.get(0.5)),
                    j.CoverageRow(label="Inside the 80% range (target 80%)", target=0.8, value=cov.get(0.8)),
                    j.CoverageRow(
                        label="Inside the 95% range (target 95%)", target=0.95, value=cov.get(0.95)
                    ),
                ],
            ),
            j.MethodSection(
                number=4,
                title="How experiments work",
                body=(
                    "I fix the plan before the first post. A coin flip, balanced within each day, decides which "
                    "posts get the change. Each post is compared with its own frozen forecast, which removes most "
                    "of the noise from topic and timing. At three posts a day, four weeks can find a 2x effect "
                    "but not a 1.25x one, so I don't pretend otherwise."
                ),
                facts=[
                    j.MethodFact(label="Per-post changes", value="Randomized per post, blocked by day"),
                    j.MethodFact(label="Timing and cadence", value="Switchback in 4-day blocks"),
                    j.MethodFact(
                        label="Confirmed means", value="Passes a running error budget across all experiments"
                    ),
                    j.MethodFact(label="Power at 4 weeks, 3 a day", value="2x: 92% · 1.5x: 50% · 1.25x: 21%"),
                ],
                coverage=[],
            ),
            j.MethodSection(
                number=5,
                title="What I can't see",
                body=(
                    "X published its ranking code (xai-org/x-algorithm), and I track every parameter change as a "
                    "possible break in my data. I still can't see who was shown a post or what X's model predicted "
                    "for them. My forecasts describe my account against its own past, not X's internal score."
                ),
                facts=[
                    j.MethodFact(
                        label="Ranker changes tracked", value="Daily check of the public parameters"
                    ),
                    j.MethodFact(label="Not observable", value="Retrieval, per-viewer scores, live config"),
                    j.MethodFact(label="Never done", value="Bought, pooled or automated engagement"),
                ],
                coverage=[],
            ),
        ],
        model_stage=stage,
        n_scored=m.owner_n,
    )


def build(
    session: Session, settings: Settings, *, preview: bool, now: datetime | None = None
) -> j.JournalSnapshot:
    t = now or datetime.now(UTC)
    ws = settings.workspace_id
    cfg = settings.journal
    handle = _handle(settings, session)
    owner = pipeline.owner_account(session, ws)
    points = follower_points(session, owner.id) if owner is not None else []
    first_run = session.scalar(
        select(Run.cutoff_at).where(Run.workspace_id == ws).order_by(Run.cutoff_at).limit(1)
    )
    last = pipeline.last_finished_run(session, ws)
    started = first_run.date() if first_run else None
    all_scored = scored_posts(session, ws, types=pipeline.FORECAST_TYPES)
    exps = [
        e
        for e in session.scalars(
            select(Experiment).where(Experiment.workspace_id == ws).order_by(Experiment.created_at)
        )
        if e.status in PUBLIC_STATUSES
    ]
    public_exps = [public_experiment(session, settings, e, t) for e in exps]
    posts_page = read_models.list_posts(session, settings, limit=ALL)
    loaded = je.load_all(settings.journal_dir)
    shown = [e for e in loaded if e.published or preview]
    entries_out: list[j.PublicEntry] = []
    for e in shown:
        t0, t1 = _day_start(e.period_start), _day_start(e.period_end + timedelta(days=1))
        week_posts = [p for p in posts_page.items if t0 <= p.created_at < t1]
        top = sorted(week_posts, key=lambda p: p.i48.value or 0.0, reverse=True)[:TOP_POSTS]
        entries_out.append(
            j.PublicEntry(
                slug=e.slug,
                week_number=e.week,
                period_start=e.period_start,
                period_end=e.period_end,
                published_at=e.published_at if e.published else None,
                draft=not e.published,
                title=e.title,
                summary=e.summary,
                body_markdown=e.body,
                stats=entry_stats(session, settings, e.period_start, e.period_end, week_posts),
                posts=[_post_row(p, handle) for p in top],
                posts_total=len(week_posts),
                experiment_keys=e.experiments,
            )
        )
    # Each experiment links back to the newest entry that mentions it.
    for x in public_exps:
        mentioned = next((e.slug for e in entries_out if x.key in e.experiment_keys), None)
        x.note_slug = mentioned
        x.related_keys = [o.key for o in public_exps if o.key != x.key][:3]
    hits = [r.in_80 for r in all_scored]
    return j.JournalSnapshot(
        preview=preview,
        site=j.PublicSite(
            handle=handle,
            x_url=f"https://x.com/{handle.lstrip('@')}",
            tagline=cfg.tagline,
            headline=cfg.headline,
            intro=cfg.intro,
            started_on=started,
            updated_at=last.cutoff_at if last else None,
            week_number=max((t.date() - started).days // 7, 0) if started else 0,
            site_url=cfg.site_url,
        ),
        stats=j.PublicStats(
            followers_now=points[-1][2] if points else None,
            followers_start=points[0][2] if points else None,
            posts_scored=len(all_scored),
            hit_80=sum(hits) / len(hits) if hits else None,
            experiments_total=len(public_exps),
            experiments_with_effect=sum(1 for x in public_exps if x.result == "effect_found"),
        ),
        followers=[j.SeriesPoint(at=at, value=float(f)) for _, at, f, _ in points],
        markers=[
            j.Marker(at=x.started_at, label=f"{x.key} started")
            for x in public_exps
            if x.started_at is not None
        ],
        experiments=list(reversed(public_exps)),
        entries=entries_out,
        method=method(session, settings),
    )


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None
