"""Per-run-interval follower data for the follower model and the funnel (MODEL §2c, batch mode).

Each interval runs from one run with a follower count to the next. Gains come from the
newest-first follower list (missing when that run's list was incomplete); impressions and
profile-click increments are summed over the owner's posts: the change in each post's
cumulative count between the two runs, or its whole count when the post first appeared in the
interval. The first run back-fills 30 days, so it opens the first interval rather than closing one.
"""

import itertools
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.db.models import FollowerSnapshot, MetricSnapshot, Post, Run
from socialmgr.services.pipeline import FORECAST_TYPES


@dataclass(frozen=True)
class RunInterval:
    run_id: int  # the run that closes the interval
    start_at: datetime
    end_at: datetime
    followers_start: int
    followers_end: int
    gains: int | None  # new followers from the follower list; None when not trusted
    impressions: int  # summed per-post increments over the interval
    profile_clicks: int | None  # None when no capture in the interval reported profile clicks
    posts: int  # owner posts (originals, quotes, replies) created in the interval

    @property
    def days(self) -> float:
        return (self.end_at - self.start_at).total_seconds() / 86400

    @property
    def net(self) -> int:
        return self.followers_end - self.followers_start

    @property
    def losses(self) -> int | None:
        """Unfollows implied by gains minus the net change (protected or suspended accounts aside)."""
        return None if self.gains is None else max(self.gains - self.net, 0)


def follower_points(
    session: Session, account_id: int, until: datetime | None = None
) -> list[tuple[int, datetime, int, int | None]]:
    """(run_id, cutoff, followers, new_followers) for each run that measured the count."""
    q = (
        select(Run.id, Run.cutoff_at, FollowerSnapshot.followers, FollowerSnapshot.new_followers)
        .join(Run, Run.id == FollowerSnapshot.run_id)
        .where(FollowerSnapshot.account_id == account_id)
        .order_by(Run.cutoff_at, Run.id)
    )
    if until is not None:
        q = q.where(Run.cutoff_at <= until)
    return [(r[0], r[1], r[2], r[3]) for r in session.execute(q)]


def run_intervals(session: Session, account_id: int, until: datetime | None = None) -> list[RunInterval]:
    points = follower_points(session, account_id, until)
    if len(points) < 2:
        return []
    run_cutoff = {run_id: cutoff for run_id, cutoff, _, _ in points}
    # Latest cumulative counts per post at each measuring run.
    rows = session.execute(
        select(
            MetricSnapshot.post_id,
            MetricSnapshot.run_id,
            MetricSnapshot.impressions,
            MetricSnapshot.profile_clicks,
        )
        .join(Post, Post.id == MetricSnapshot.post_id)
        .where(
            Post.account_id == account_id,
            Post.type.in_(FORECAST_TYPES),
            MetricSnapshot.run_id.in_(list(run_cutoff)),
        )
        .order_by(MetricSnapshot.id)
    ).all()
    by_run: dict[int, dict[int, tuple[int | None, int | None]]] = {}
    for post_id, run_id, imp, pc in rows:
        by_run.setdefault(run_id, {})[post_id] = (imp, pc)
    created = dict(
        session.execute(
            select(Post.id, Post.created_at).where(
                Post.account_id == account_id, Post.type.in_(FORECAST_TYPES), Post.deleted_at.is_(None)
            )
        ).all()
    )

    intervals: list[RunInterval] = []
    last_seen: dict[int, tuple[int | None, int | None]] = dict(by_run.get(points[0][0], {}))
    for (_, start, f0, _), (run_id, end, f1, gains) in itertools.pairwise(points):
        impressions = 0
        clicks = 0
        clicks_seen = False
        for post_id, (imp, pc) in by_run.get(run_id, {}).items():
            prev_imp, prev_pc = last_seen.get(post_id, (0, 0))
            if imp is not None:
                impressions += max(imp - (prev_imp or 0), 0)
            if pc is not None:
                clicks_seen = True
                clicks += max(pc - (prev_pc or 0), 0)
            last_seen[post_id] = (
                imp if imp is not None else prev_imp,
                pc if pc is not None else prev_pc,
            )
        intervals.append(
            RunInterval(
                run_id=run_id,
                start_at=start,
                end_at=end,
                followers_start=f0,
                followers_end=f1,
                gains=gains,
                impressions=impressions,
                profile_clicks=clicks if clicks_seen else None,
                posts=sum(1 for t in created.values() if start < t <= end),
            )
        )
    return intervals
