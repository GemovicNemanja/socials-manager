"""Inputs to the follower model: one record per run interval (MODEL.md §2c, batch mode).

A run k records the follower count F_k, the new follower ids since the last run (gains G_k, which
may be missing or incomplete), and every owner post's cumulative impressions and profile clicks.
The interval (t_{k-1}, t_k] then carries F at both ends, G_k when complete, and the per-interval
increments I_k and PC_k: the sum over posts of the change in each count between the two runs.
"""

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SECONDS_PER_DAY = 86_400.0


def _aware(ts: datetime, name: str) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")


def _opt_float(v: Any) -> float | None:
    return None if v is None else float(v)


@dataclass(frozen=True)
class RunInterval:
    """One run interval (start, end]. ``gains`` is G_k from the newest-first follower list.

    ``gains_complete`` false (or ``gains`` None) keeps the interval out of the gain likelihood; its
    net change F_end - F_start is still used. ``impressions`` and ``profile_clicks`` are the
    per-interval increments summed over posts (``sum_increments``); None when not collected.
    """

    start: datetime
    end: datetime
    followers_start: int
    followers_end: int
    gains: int | None = None
    gains_complete: bool = True
    impressions: float | None = None
    profile_clicks: float | None = None

    def __post_init__(self) -> None:
        _aware(self.start, "start")
        _aware(self.end, "end")
        if self.end <= self.start:
            raise ValueError("interval end must be after its start")
        if self.followers_start < 0 or self.followers_end < 0:
            raise ValueError("follower counts must be non-negative")
        if self.gains is not None and self.gains < 0:
            raise ValueError("gains must be non-negative")

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / SECONDS_PER_DAY

    @property
    def net_change(self) -> int:
        return self.followers_end - self.followers_start

    @property
    def gains_observed(self) -> bool:
        return self.gains is not None and self.gains_complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "followers_start": self.followers_start,
            "followers_end": self.followers_end,
            "gains": self.gains,
            "gains_complete": self.gains_complete,
            "impressions": self.impressions,
            "profile_clicks": self.profile_clicks,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RunInterval":
        return cls(
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
            followers_start=int(d["followers_start"]),
            followers_end=int(d["followers_end"]),
            gains=None if d.get("gains") is None else int(d["gains"]),
            gains_complete=bool(d.get("gains_complete", True)),
            impressions=_opt_float(d.get("impressions")),
            profile_clicks=_opt_float(d.get("profile_clicks")),
        )


@dataclass(frozen=True)
class RunObservation:
    """What one run recorded. ``impressions``/``profile_clicks`` are increments since the previous run."""

    fetched_at: datetime
    followers: int
    new_followers: int | None = None
    new_followers_complete: bool = True
    impressions: float | None = None
    profile_clicks: float | None = None


def intervals_from_runs(runs: Sequence[RunObservation]) -> list[RunInterval]:
    """Pair consecutive runs into intervals. The first run only supplies the starting count."""
    ordered = sorted(runs, key=lambda r: r.fetched_at)
    out: list[RunInterval] = []
    for prev, cur in itertools.pairwise(ordered):
        if cur.fetched_at <= prev.fetched_at:
            continue
        out.append(
            RunInterval(
                start=prev.fetched_at,
                end=cur.fetched_at,
                followers_start=prev.followers,
                followers_end=cur.followers,
                gains=cur.new_followers,
                gains_complete=cur.new_followers_complete,
                impressions=cur.impressions,
                profile_clicks=cur.profile_clicks,
            )
        )
    return out


def sum_increments(previous: Mapping[str, int | None], current: Mapping[str, int | None]) -> float | None:
    """Sum over posts of max(current - previous, 0); a post absent from ``previous`` counts from 0.

    Pass, per post, the cumulative count at this run and the last known count before it. Returns
    None when no post has a current value (the metric wasn't collected this run).
    """
    total = 0.0
    seen = False
    for post, cur in current.items():
        if cur is None:
            continue
        seen = True
        prev = previous.get(post)
        total += max(float(cur) - float(prev or 0), 0.0)
    return total if seen else None


def finite(v: float | None) -> bool:
    return v is not None and math.isfinite(v)
