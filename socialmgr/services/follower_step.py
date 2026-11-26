"""The run's followers step: follower model fit, frozen follower forecasts, scores, goal status.

Placeholder until the follower engine lands; ``preview`` answers that the model isn't ready.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from socialmgr.api import health_schemas as h
from socialmgr.core.config import Settings


def preview(session: Session, settings: Settings, body: h.GoalIn, now: datetime) -> h.GoalPreviewOut:
    return h.GoalPreviewOut(
        status="no_goal",
        reason="awaiting_model",
        p_goal=None,
        median=None,
        q10=None,
        q90=None,
        paths=0,
        summary="The follower model hasn't run yet.",
    )
