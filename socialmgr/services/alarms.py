"""Detector alarms (MODEL §4.3): listing and acknowledging. Acknowledging changes nothing else."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.db.models import Alarm


class AlarmError(ValueError):
    pass


def open_alarms(session: Session, workspace_id: int) -> list[Alarm]:
    return list(
        session.scalars(
            select(Alarm)
            .where(Alarm.workspace_id == workspace_id, Alarm.acknowledged_at.is_(None))
            .order_by(Alarm.raised_at.desc())
        )
    )


def recent_alarms(session: Session, workspace_id: int, limit: int = 20) -> list[Alarm]:
    return list(
        session.scalars(
            select(Alarm)
            .where(Alarm.workspace_id == workspace_id)
            .order_by(Alarm.raised_at.desc(), Alarm.id.desc())
            .limit(limit)
        )
    )


def acknowledge(
    session: Session, workspace_id: int, alarm_id: int, by: str, note: str | None, now: datetime
) -> Alarm:
    """Mark an alarm as seen, with an optional note. Charts, intervals and models are untouched."""
    alarm = session.get(Alarm, alarm_id)
    if alarm is None or alarm.workspace_id != workspace_id:
        raise AlarmError(f"There is no alarm:{alarm_id}.")
    if alarm.acknowledged_at is not None:
        raise AlarmError(f"alarm:{alarm_id} was already acknowledged on {alarm.acknowledged_at:%b %d}.")
    alarm.acknowledged_at = now
    alarm.acknowledged_by = by
    alarm.note = note
    session.flush()
    return alarm
