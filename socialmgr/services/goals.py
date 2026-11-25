"""Goals the member sets (MODEL §4.2; plan §6: no default goals in v1).

A goal is frozen per period: setting a new one supersedes the current goal of that scope and
starts a new period. Nothing is deleted, so every change stays in the log.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Goal
from socialmgr.services.pipeline import owner_account

GoalScope = Literal["posts", "followers"]
METRIC = {"posts": "impressions_48h", "followers": "followers"}
MAX_FOLLOWER_HORIZON_DAYS = 365


class GoalError(ValueError):
    """A goal that can't be set, with a reason the caller can show."""


@dataclass(frozen=True)
class GoalRequest:
    scope: GoalScope
    target: float
    target_at: datetime | None = None  # followers: the date F should reach the target
    note: str | None = None


def current_goals(session: Session, workspace_id: int) -> dict[str, Goal]:
    rows = session.scalars(
        select(Goal)
        .where(Goal.workspace_id == workspace_id, Goal.superseded_at.is_(None))
        .order_by(Goal.created_at)
    )
    return {g.scope: g for g in rows}


def goal_history(session: Session, workspace_id: int, scope: str | None = None) -> list[Goal]:
    q = select(Goal).where(Goal.workspace_id == workspace_id).order_by(Goal.created_at.desc())
    if scope is not None:
        q = q.where(Goal.scope == scope)
    return list(session.scalars(q))


def validate(request: GoalRequest, now: datetime) -> None:
    if request.scope not in METRIC:
        raise GoalError(f"'{request.scope}' isn't a goal scope; use posts or followers.")
    if not request.target > 0:
        raise GoalError("A goal's target must be above zero.")
    if request.scope == "followers":
        if request.target_at is None:
            raise GoalError("A follower goal needs a date to reach it by.")
        if request.target_at <= now:
            raise GoalError("A follower goal's date must be in the future.")
        if (request.target_at - now).days > MAX_FOLLOWER_HORIZON_DAYS:
            raise GoalError("Follower goals reach at most a year ahead.")
    elif request.target_at is not None:
        raise GoalError("A posts goal is a 48h impressions target per post; it takes no date.")


def set_goal(
    session: Session, settings: Settings, request: GoalRequest, created_by: str, now: datetime
) -> Goal:
    """Supersede the scope's current goal (if any) and start a new period with this one."""
    validate(request, now)
    owner = owner_account(session, settings.workspace_id)
    if owner is None:
        raise GoalError("Connect the account and run once before setting a goal.")
    previous = current_goals(session, settings.workspace_id).get(request.scope)
    if previous is not None:
        previous.superseded_at = now
    goal = Goal(
        workspace_id=settings.workspace_id,
        account_id=owner.id,
        scope=request.scope,
        metric=METRIC[request.scope],
        target=float(request.target),
        horizon_days=(
            max((request.target_at - now).days, 1)
            if request.scope == "followers" and request.target_at
            else None
        ),
        target_at=request.target_at,
        created_by=created_by,
        created_at=now,
        superseded_at=None,
        note=request.note,
    )
    session.add(goal)
    session.flush()
    return goal
