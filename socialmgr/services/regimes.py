"""The regime step: seed the registry once, then open a regime when ``param.rs`` changes."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.db.models import Regime, TrackerState
from socialmgr.modelling.regimes import SEED_DATES, TRACKER_NAME, Fetcher


@dataclass
class RegimeSummary:
    seeded: int = 0
    opened: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def seed_regimes(session: Session, settings: Settings, now: datetime) -> int:
    added = 0
    platform = settings.account.platform
    for day, kind in SEED_DATES:
        started = datetime.fromisoformat(day).replace(tzinfo=UTC)
        exists = session.scalar(
            select(Regime.id).where(
                Regime.workspace_id == settings.workspace_id,
                Regime.platform == platform,
                Regime.started_at == started,
                Regime.source == "seed",
            )
        )
        if exists is None:
            session.add(
                Regime(
                    workspace_id=settings.workspace_id,
                    platform=platform,
                    started_at=started,
                    kind=kind,
                    source="seed",
                    confidence="high",
                    ref=None,
                    description=f"Seeded ranker change ({kind}), MODEL §2e",
                    # Known since the registry existed: available to every as-of forecast.
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            added += 1
    session.flush()
    return added


def check_regimes(
    session: Session, settings: Settings, fetcher: Fetcher | None, now: datetime
) -> RegimeSummary:
    summary = RegimeSummary(seeded=seed_regimes(session, settings, now))
    if fetcher is None:
        return summary
    try:
        latest = fetcher()
    except Exception as exc:  # network or rate limit: the run goes on, the next run checks again
        summary.caveats.append(f"Couldn't check param.rs for ranker changes: {exc}")
        return summary
    if latest is None:
        return summary
    state = session.scalar(select(TrackerState).where(TrackerState.name == TRACKER_NAME))
    if state is None:
        # First check: record the baseline; history before it is covered by the seeded dates.
        session.add(
            TrackerState(
                workspace_id=settings.workspace_id, name=TRACKER_NAME, ref=latest.ref, checked_at=now
            )
        )
    elif state.ref != latest.ref:
        session.add(
            Regime(
                workspace_id=settings.workspace_id,
                platform=settings.account.platform,
                started_at=latest.committed_at,
                kind="unknown",
                source="param_rs",
                confidence="high",
                ref=latest.ref[:64],
                description=f"param.rs changed: {latest.message}",
                created_at=now,
            )
        )
        state.ref = latest.ref
        state.checked_at = now
        summary.opened.append(latest.ref[:12])
    else:
        state.checked_at = now
    session.flush()
    return summary
