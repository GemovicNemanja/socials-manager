"""Scored outcomes the model-health views and monitors read (MODEL §4.3, §5).

One row per matured owner post: its as-of forecast (subject ``post:<id>``) and that forecast's
score. Live forecasts of planned slots are scored too, but calibration reads the as-of ones so
every post counts once and each was made by the same rule.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.db.models import Forecast, Post, Run, ScoreRow
from socialmgr.services.pipeline import owner_account

CALIBRATION_TYPES = ("original", "quote")  # D1 runs on originals and quotes (MODEL §4.3)


@dataclass(frozen=True)
class ScoredPost:
    post_id: int
    created_at: datetime
    type: str
    forecast_id: int
    model_version: str
    kind: str
    pit_mid: float
    pit_rand: float
    crps_log: float
    in_50: bool
    in_80: bool
    outcome: float
    q50: float
    scored_at: datetime
    run_id: int  # the run that scored it


def scored_posts(
    session: Session,
    workspace_id: int,
    types: tuple[str, ...] = CALIBRATION_TYPES,
    until: datetime | None = None,
) -> list[ScoredPost]:
    """Scored as-of forecasts of the owner's posts, oldest post first.

    ``until`` keeps scores made by runs with a cutoff at or before it (what a run could see).
    """
    owner = owner_account(session, workspace_id)
    if owner is None:
        return []
    q = (
        select(Post, Forecast, ScoreRow)
        .join(Forecast, Forecast.subject == "post:" + Post.id.cast(Forecast.subject.type))
        .join(ScoreRow, ScoreRow.forecast_id == Forecast.id)
        .join(Run, Run.id == ScoreRow.run_id)
        .where(
            Post.account_id == owner.id,
            Post.deleted_at.is_(None),
            Post.type.in_(types),
            Forecast.workspace_id == workspace_id,
        )
        .order_by(Post.created_at, Post.id)
    )
    if until is not None:
        q = q.where(Run.cutoff_at <= until)
    return [
        ScoredPost(
            post_id=post.id,
            created_at=post.created_at,
            type=post.type,
            forecast_id=fc.id,
            model_version=fc.model_version,
            kind=fc.kind,
            pit_mid=sc.pit_mid,
            pit_rand=sc.pit_rand,
            crps_log=sc.crps_log,
            in_50=sc.in_50,
            in_80=sc.in_80,
            outcome=sc.outcome,
            q50=float(fc.quantiles["q50"]),
            scored_at=sc.scored_at,
            run_id=sc.run_id,
        )
        for post, fc, sc in session.execute(q)
    ]
