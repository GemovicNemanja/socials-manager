# ruff: noqa: E501  (the week-0 draft is markdown prose; one paragraph per line)
"""Draft weekly entries from the week's data (the owner edits, then publishes).

Week 0 introduces the plan. Later weeks start from that week's numbers: follower change against the
frozen follower forecast, impressions, how many posts landed inside their range, the best and worst
post against its forecast, and the experiments that ran. The prose is a starting point with TODO
markers where only the owner knows the story; publishing refuses an entry that still has them.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Run
from socialmgr.journal import entries as je
from socialmgr.journal import snapshot
from socialmgr.services import read_models

WEEK0_TITLE = "Week 0: I'm growing my X account in public, and forecasting every post first"


def week_bounds(started_on: date, week: int) -> tuple[date, date]:
    start = started_on + timedelta(days=7 * week)
    return start, start + timedelta(days=6)


def started_on(session: Session, settings: Settings) -> date | None:
    first = session.scalar(
        select(Run.cutoff_at)
        .where(Run.workspace_id == settings.workspace_id)
        .order_by(Run.cutoff_at)
        .limit(1)
    )
    return first.date() if first else None


def week0_body(snap_followers: int | None, handle: str) -> str:
    followers = f"{snap_followers:,} followers" if snap_followers is not None else "a handful of followers"
    return f"""I'm starting to grow {handle} in public, from {followers}. This first note explains the plan; every week after this one reports how it went, including what didn't work.

## The rule: forecast first, then post

Before each post goes out, the system freezes a forecast of how it should do: a range for its impressions 48 hours after posting, with the inputs hashed so I can't quietly change it later. Two days later the post is scored against that range: above, as expected, or below. Over time that tells me two things: which posts beat my own expectations, and whether my expectations are honest. If they are, about 8 in 10 posts should land inside their 80% range.

## Small, randomized experiments

Instead of guessing what works, I'll test one or two changes at a time: an outcome-first hook against my usual opening, an image against text only, a link in the post against a link in the first reply. A coin flip, balanced within each day, decides which posts get the change, and each post is compared with its own frozen forecast. That cancels out most of the noise from topic and timing. The plan for each experiment is written down before the first post, and I'll publish the ones that find nothing too.

## What I'll publish here

- A weekly note: follower change against what I expected, impressions, how many posts landed inside their range, and what I learned.
- Every experiment: the question, the setup, the result, and what I changed because of it.
- How the numbers are made, and how far to trust them (see Method).

## Where it starts

Right now the forecasts come from generic starting assumptions, not from my own posts, so they will be rough. Once 40 of my posts have matured, a model fitted on my own account is tested against a simple baseline and takes over only if it passes every check. The first goal is simple: post daily, mostly originals, and get there.

TODO: add a sentence in your own words on why you're doing this and what you hope to learn.
"""


def draft_week(
    session: Session, settings: Settings, week: int | None = None, now: datetime | None = None
) -> je.Entry:
    """Write (or overwrite an unpublished) draft for ``week``; the latest finished week by default."""
    t = now or datetime.now(UTC)
    start_day = started_on(session, settings)
    if start_day is None:
        raise je.EntryError("Run once before drafting an entry: the journal starts at the first run.")
    current = max((t.date() - start_day).days // 7, 0)
    if week is None:
        week = 0 if current == 0 else current - 1
    if week < 0 or week > current:
        raise je.EntryError(f"Week {week} isn't between 0 and the current week ({current}).")
    path = je.path_for(settings.journal_dir, week)
    existing = next((e for e in je.load_all(settings.journal_dir) if e.week == week), None)
    if existing is not None and existing.published:
        raise je.EntryError(f"Week {week} is already published; edit {existing.path.name} by hand if needed.")
    period_start, period_end = week_bounds(start_day, week)
    snap = snapshot.build(session, settings, preview=True, now=t)
    if week == 0:
        title = WEEK0_TITLE
        body = week0_body(snap.stats.followers_start, snap.site.handle)
        keys: list[str] = []
    else:
        title, body, keys = _week_text(session, settings, snap, period_start, period_end)
    entry = je.Entry(
        path=path,
        title=title,
        week=week,
        period_start=period_start,
        period_end=period_end,
        status="draft",
        published_at=None,
        experiments=keys,
        body=body,
    )
    je.save(entry)
    return entry


def _week_text(
    session: Session, settings: Settings, snap: object, start: date, end: date
) -> tuple[str, str, list[str]]:
    from socialmgr.journal.schema import JournalSnapshot

    assert isinstance(snap, JournalSnapshot)
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    page = read_models.list_posts(session, settings, limit=1_000_000)
    week_posts = [p for p in page.items if t0 <= p.created_at < t1]
    stats = snapshot.entry_stats(session, settings, start, end, week_posts)
    lines: list[str] = []
    if stats.followers_delta is not None:
        exp = (
            f" (I expected {stats.followers_expected[0]:+,.0f} to {stats.followers_expected[1]:+,.0f})"
            if stats.followers_expected
            else ""
        )
        lines.append(f"Followers: {stats.followers_delta:+,}{exp}.")
    if stats.impressions is not None:
        change = (
            f", {stats.impressions_change:+.0%} on the week before"
            if stats.impressions_change is not None
            else ""
        )
        lines.append(f"Impressions: {stats.impressions:,.0f}{change}.")
    if stats.posts_scored:
        lines.append(
            f"{stats.posts_in_80} of {stats.posts_scored} matured posts landed inside their 80% range."
        )
    scored = [p for p in week_posts if p.status in ("above", "below") and p.forecast is not None]
    best = max(scored, key=lambda p: p.pit_mid or 0.0, default=None)
    worst = min(scored, key=lambda p: p.pit_mid or 1.0, default=None)
    notable = []
    if best is not None and best.status == "above":
        notable.append(
            f"- Best against its forecast: “{_snip(best.text)}”, about {best.i48.value or 0:,.0f} "
            f"impressions at 48h against a median of {best.forecast.q50 if best.forecast else 0:,.0f}."
        )
    if worst is not None and worst.status == "below":
        notable.append(
            f"- Furthest below: “{_snip(worst.text)}”, about {worst.i48.value or 0:,.0f} against "
            f"{worst.forecast.q50 if worst.forecast else 0:,.0f}."
        )
    running = [x for x in snap.experiments if x.started_at is not None and x.started_at.date() <= end]
    exp_lines = [
        f"- {x.key} ({x.name}): {x.progress_label} posts; "
        + (
            f"{x.effect_ratio:.2f}x so far, 80% interval {x.effect_ci80[0]:.2f}-{x.effect_ci80[1]:.2f}."
            if x.effect_ratio is not None and x.effect_ci80
            else "too early for an estimate."
        )
        for x in running
    ]
    title = "TODO: a title that says what this week taught you"
    body = "\n\n".join(
        part
        for part in (
            "TODO: two or three sentences on the week: what you tried, what surprised you.",
            " ".join(lines),
            "\n".join(notable) if notable else "",
            ("## Experiments\n\n" + "\n".join(exp_lines)) if exp_lines else "",
            "TODO: what you'll change next week, and why.",
        )
        if part
    )
    return title, body, [x.key for x in running]


def _snip(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"
