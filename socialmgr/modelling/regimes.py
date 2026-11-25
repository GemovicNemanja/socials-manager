"""Ranker regimes (MODEL.md §2e): seeded change dates plus changes to X's published ``param.rs``.

v1 tracks one file, ``home-mixer/params/param.rs`` in ``xai-org/x-algorithm``, through the GitHub API:
a new commit touching it opens a regime dated at the commit. Classifying the change (weight,
cold-start, source, model, safety), sync-header dates and revert down-weighting come later.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

REPO = "xai-org/x-algorithm"
PARAM_PATH = "home-mixer/params/param.rs"
TRACKER_NAME = f"github:{REPO}:{PARAM_PATH}"

# MODEL §2e seed dates (2026). "10-08/13" is entered as both days.
SEED_DATES: tuple[tuple[str, str], ...] = (
    ("2026-08-13", "unknown"),
    ("2026-08-25", "unknown"),
    ("2026-09-18", "unknown"),
    ("2026-09-29", "unknown"),
    ("2026-09-30", "cold_start"),
    ("2026-10-07", "unknown"),
    ("2026-10-08", "unknown"),
    ("2026-10-13", "unknown"),
    ("2026-10-14", "unknown"),
    ("2026-10-22", "unknown"),
    ("2026-11-05", "unknown"),
    ("2026-11-13", "unknown"),
    ("2026-11-18", "unknown"),
)


@dataclass(frozen=True)
class FileVersion:
    ref: str  # commit sha
    committed_at: datetime
    message: str


Fetcher = Callable[[], FileVersion | None]


def github_fetcher(client: httpx.Client | None = None, timeout: float = 10.0) -> Fetcher:
    """Latest commit touching ``param.rs``, via the unauthenticated GitHub API (60 requests/hour)."""

    def fetch() -> FileVersion | None:
        http = client or httpx.Client(timeout=timeout)
        try:
            resp = http.get(
                f"https://api.github.com/repos/{REPO}/commits",
                params={"path": PARAM_PATH, "per_page": 1},
                headers={"Accept": "application/vnd.github+json", "User-Agent": "socialmgr"},
            )
            resp.raise_for_status()
            commits = resp.json()
        finally:
            if client is None:
                http.close()
        if not commits:
            return None
        c = commits[0]
        when = datetime.fromisoformat(c["commit"]["committer"]["date"].replace("Z", "+00:00")).astimezone(UTC)
        return FileVersion(
            ref=c["sha"], committed_at=when, message=c["commit"]["message"].splitlines()[0][:200]
        )

    return fetch
