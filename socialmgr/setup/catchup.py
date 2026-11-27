"""When the daily job should actually run.

launchd starts the job at its time (or on wake, if the Mac slept through it) and at every login.
The login start catches up a day the Mac was off or logged out at that time; it does nothing when
a run already finished since the most recent scheduled time. Woken from sleep, the Mac often has
no network for a few seconds, so the job waits for X to be reachable before starting.
"""

import socket
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.db.models import Run
from socialmgr.setup.macos import DailyRun

X_HOST = ("api.x.com", 443)


def last_slot(at: DailyRun, now_local: datetime) -> datetime:
    """The most recent scheduled time at or before ``now_local`` (an aware local time)."""
    slot = now_local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return slot if slot <= now_local else slot - timedelta(days=1)


def ran_since(session: Session, workspace_id: int, since: datetime) -> bool:
    finished = session.scalar(
        select(func.max(Run.finished_at)).where(Run.workspace_id == workspace_id, Run.status == "finished")
    )
    return finished is not None and finished >= since


def wait_for_network(
    timeout_s: float = 600.0,
    every_s: float = 15.0,
    connect: Callable[..., object] = socket.create_connection,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """True once X answers a TCP connection; False after ``timeout_s`` without one."""
    deadline = clock() + timeout_s
    while True:
        try:
            conn = connect(X_HOST, timeout=5)
        except OSError:
            if clock() >= deadline:
                return False
            sleep(every_s)
            continue
        close = getattr(conn, "close", None)
        if callable(close):
            close()
        return True
