"""Run intervals for the follower model (MODEL §2c batch mode) on the fixture workspace."""

import itertools

from sqlalchemy.orm import Session, sessionmaker

from socialmgr.services.follower_data import follower_points, run_intervals
from socialmgr.services.pipeline import owner_account


def test_intervals_chain_follower_counts_and_sum_increments(loaded: sessionmaker[Session]) -> None:
    with loaded() as session:
        owner = owner_account(session, 1)
        assert owner is not None
        points = follower_points(session, owner.id)
        intervals = run_intervals(session, owner.id)
        assert len(intervals) == len(points) - 1 > 20
        for a, b in itertools.pairwise(intervals):
            assert a.end_at == b.start_at and a.followers_end == b.followers_start
        assert all(i.days > 0 and i.impressions >= 0 for i in intervals)
        assert sum(i.impressions for i in intervals) > 0
        assert any(i.gains is not None for i in intervals)
        for i in intervals:
            if i.gains is not None:
                assert i.losses is not None and i.losses >= 0


def test_scored_posts_are_as_of_originals_once_each(loaded: sessionmaker[Session]) -> None:
    from socialmgr.services.model_data import scored_posts

    with loaded() as session:
        rows = scored_posts(session, 1)
        assert len(rows) > 60
        assert len({r.post_id for r in rows}) == len(rows)
        assert {r.type for r in rows} <= {"original", "quote"}
        assert all(0 <= r.pit_rand <= 1 for r in rows)
        assert [r.created_at for r in rows] == sorted(r.created_at for r in rows)
