"""One run of all monitors: persisted state in, next state and a renderable report out (MODEL §4.3)."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Self

import numpy as np
from numpy.typing import ArrayLike

from socialmgr.modelling.monitors.aci import AciState
from socialmgr.modelling.monitors.charts import (
    ARMING_POSTS,
    PIT_EPS,
    Alarm,
    CusumChart,
    follower_chart,
    location_chart,
    pit_to_z,
    scale_chart,
)
from socialmgr.modelling.monitors.diagnostics import (
    DIAGNOSTIC_EVERY,
    DIAGNOSTIC_WINDOW,
    Diagnostics,
    diagnostics_due,
    run_diagnostics,
)
from socialmgr.modelling.monitors.goal import D2_MIN_POSTS, D2_WINDOW, GoalCheck, check_goal


@dataclass(frozen=True)
class MonitorState:
    """Everything the monitors carry from one run to the next."""

    location: CusumChart
    scale: CusumChart
    follower: CusumChart
    aci: AciState
    posts_seen: int = 0  # matured posts fed in, armed or not
    last_diagnostics_at: int = 0
    recent_pit: tuple[float, ...] = ()  # the last DIAGNOSTIC_WINDOW randomized PITs

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location.to_dict(),
            "scale": self.scale.to_dict(),
            "follower": self.follower.to_dict(),
            "aci": self.aci.to_dict(),
            "posts_seen": self.posts_seen,
            "last_diagnostics_at": self.last_diagnostics_at,
            "recent_pit": list(self.recent_pit),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            location=CusumChart.from_dict(data["location"]),
            scale=CusumChart.from_dict(data["scale"]),
            follower=CusumChart.from_dict(data["follower"]),
            aci=AciState.from_dict(data["aci"]),
            posts_seen=data["posts_seen"],
            last_diagnostics_at=data["last_diagnostics_at"],
            recent_pit=tuple(data["recent_pit"]),
        )


def initial_state(gap_days: float) -> MonitorState:
    """Fresh, disarmed monitors at the deployed thresholds."""
    return MonitorState(
        location=location_chart(), scale=scale_chart(), follower=follower_chart(gap_days), aci=AciState()
    )


@dataclass(frozen=True)
class ChartSummary:
    name: str
    kind: str  # "cusum" or "aci"
    armed: bool
    n: int
    statistics: dict[str, float]
    thresholds: dict[str, float]
    target_arl0: float | None
    alarm_count: int
    last_alarm_index: int | None
    alarmed_this_run: bool


@dataclass(frozen=True)
class MonitorReport:
    """What the briefing and UI render for one run."""

    posts_seen: int
    armed: bool
    d1_quiet: bool
    charts: tuple[ChartSummary, ...]
    alarms: tuple[Alarm, ...] = ()
    diagnostics: Diagnostics | None = None
    goal: GoalCheck | None = None
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "posts_seen": self.posts_seen,
            "armed": self.armed,
            "d1_quiet": self.d1_quiet,
            "charts": [asdict(c) for c in self.charts],
            "alarms": [a.to_dict() for a in self.alarms],
            "diagnostics": None if self.diagnostics is None else self.diagnostics.to_dict(),
            "goal": None if self.goal is None else self.goal.to_dict(),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        diag, goal = data.get("diagnostics"), data.get("goal")
        return cls(
            posts_seen=data["posts_seen"],
            armed=data["armed"],
            d1_quiet=data["d1_quiet"],
            charts=tuple(ChartSummary(**c) for c in data["charts"]),
            alarms=tuple(Alarm.from_dict(a) for a in data["alarms"]),
            diagnostics=None if diag is None else Diagnostics.from_dict(diag),
            goal=None if goal is None else GoalCheck.from_dict(goal),
            notes=tuple(data["notes"]),
        )


def _cusum_summary(chart: CusumChart, alarms: Sequence[Alarm]) -> ChartSummary:
    return ChartSummary(
        name=chart.name,
        kind="cusum",
        armed=chart.armed,
        n=chart.n,
        statistics={"upper": chart.s_upper, "lower": chart.s_lower},
        thresholds={"upper": chart.h_upper, "lower": chart.h_lower, "k": chart.k},
        target_arl0=chart.target_arl0,
        alarm_count=chart.alarm_count,
        last_alarm_index=chart.last_alarm_index,
        alarmed_this_run=any(a.chart == chart.name for a in alarms),
    )


def _aci_summary(aci: AciState, alarms: Sequence[Alarm]) -> ChartSummary:
    return ChartSummary(
        name=aci.name,
        kind="aci",
        armed=aci.armed,
        n=aci.n,
        statistics={"alpha_t": aci.alpha_t, "low_streak": float(aci.low_streak)},
        thresholds={"alarm_level": aci.alarm_level, "alarm_run": float(aci.alarm_run)},
        target_arl0=None,
        alarm_count=aci.alarm_count,
        last_alarm_index=aci.last_alarm_index,
        alarmed_this_run=any(a.chart == aci.name for a in alarms),
    )


@dataclass(frozen=True)
class GoalInputs:
    """The last ≥ 30 matured posts' frozen predictive draws (posts × draws) and outcomes, plus the goal."""

    draws: ArrayLike
    outcomes: ArrayLike
    goal: float


def run_monitors(
    state: MonitorState,
    *,
    post_pits: Sequence[float],
    follower_pits: Sequence[float],
    matured_owner_posts: int,
    gap_days: float,
    follower_armed: bool | None = None,
    goal: GoalInputs | None = None,
    eps: float = PIT_EPS,
) -> tuple[MonitorState, MonitorReport]:
    """Feed this run's newly matured observations to every monitor.

    ``post_pits`` are the randomized PITs of I48 for originals and quotes that matured since the last run,
    in maturity order; ``follower_pits`` the randomized PITs of follower gains per run interval since the
    last run. Post charts and D3 are armed for the whole run once ``matured_owner_posts`` ≥ 20; the
    follower chart follows the same rule unless ``follower_armed`` says otherwise. D1 is quiet when neither
    post chart alarmed in the last 30 posts; D2 runs only then, from 60 matured posts, and only when
    ``goal`` is given.
    """
    armed = matured_owner_posts >= ARMING_POSTS
    f_armed = armed if follower_armed is None else follower_armed
    alarms: list[Alarm] = []
    notes: list[str] = []

    follower = state.follower
    if follower.gap_days != gap_days:
        follower = follower.with_gap(gap_days)
        notes.append(f"follower thresholds recomputed for G = {gap_days:g} days (h = {follower.h_upper:.2f})")

    location, scale, aci = state.location, state.scale, state.aci
    for pit in post_pits:
        z = pit_to_z(pit, eps)
        location, a1 = location.update(z, armed=armed)
        scale, a2 = scale.update(z, armed=armed)
        aci, a3 = aci.update(pit, armed=armed)
        alarms.extend(a for a in (a1, a2, a3) if a is not None)
    for pit in follower_pits:
        follower, a4 = follower.update(pit_to_z(pit, eps), armed=f_armed)
        if a4 is not None:
            alarms.append(a4)

    posts_seen = state.posts_seen + len(post_pits)
    recent = (state.recent_pit + tuple(float(p) for p in post_pits))[-DIAGNOSTIC_WINDOW:]
    diagnostics = None
    last_diag = state.last_diagnostics_at
    if armed and diagnostics_due(posts_seen, last_diag) and len(recent) >= DIAGNOSTIC_EVERY:
        diagnostics = run_diagnostics(np.asarray(recent), at_post=posts_seen, eps=eps)
        last_diag = posts_seen

    d1_quiet = all(
        c.last_alarm_index is None or c.n - c.last_alarm_index >= D2_WINDOW for c in (location, scale)
    )
    goal_check = None
    if goal is not None and matured_owner_posts >= D2_MIN_POSTS:
        goal_check = check_goal(goal.draws, goal.outcomes, goal.goal, d1_quiet=d1_quiet)

    new_state = replace(
        state,
        location=location,
        scale=scale,
        follower=follower,
        aci=aci,
        posts_seen=posts_seen,
        last_diagnostics_at=last_diag,
        recent_pit=recent,
    )
    report = MonitorReport(
        posts_seen=posts_seen,
        armed=armed,
        d1_quiet=d1_quiet,
        charts=(
            _cusum_summary(location, alarms),
            _cusum_summary(scale, alarms),
            _cusum_summary(follower, alarms),
            _aci_summary(aci, alarms),
        ),
        alarms=tuple(alarms),
        diagnostics=diagnostics,
        goal=goal_check,
        notes=tuple(notes),
    )
    return new_state, report
