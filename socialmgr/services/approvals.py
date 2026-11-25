"""Owner-only decisions on MODEL §6.9's approval items.

Reachable from the CLI (``socialmgr approve`` / ``socialmgr reject``) and the web app only. The MCP
server and ``services/experiments.py`` must never import this module: an agent can't approve its
own proposal or amendment (``tests/test_experiments.py`` checks both).
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.experiments import engine
from socialmgr.services.experiments import ApprovalOut, approval_out
from socialmgr.services.plugins import ActivePlugins

OWNER = "owner"


def decide(
    session: Session,
    settings: Settings,
    approval_id: int,
    decision: str,
    note: str | None = None,
    plugins: ActivePlugins | None = None,
    now: datetime | None = None,
) -> ApprovalOut:
    """Approve or reject as the owner and commit. Raises ``engine.ExperimentError`` when the item
    isn't pending or can't be applied."""
    row = engine.decide_approval(
        session,
        settings,
        approval_id,
        decision,
        actor=OWNER,
        now=now or datetime.now(UTC).replace(microsecond=0),
        note=note,
        plugins=plugins,
    )
    session.commit()
    return approval_out(session, row)
