"""Stored X credentials and the locked refresh (IMPLEMENTATION_PLAN.md §5, "Rules").

X refresh tokens are single-use. Two processes refreshing with the same token would leave one of
them holding a dead token, and a refresh whose result isn't stored loses the account. So:

1. every read-decide-refresh-store sequence runs under a process-exclusive ``fcntl.flock`` on a
   file in ``data_dir`` (macOS/Linux);
2. before calling X, the refresh claims the SQLite write lock; if it can't, nothing is sent;
3. the new tokens are committed before the access token is handed to anyone.
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.db.models import Credential
from socialmgr.providers.x.auth import TokenSet, refresh_tokens
from socialmgr.providers.x.errors import OAuthError, ReauthRequired, TokenStoreBusy, XUnavailable

PROVIDER = "x"
LOCK_FILE = "x-tokens.lock"
REFRESH_MARGIN = timedelta(minutes=5)


@contextmanager
def token_lock(data_dir: Path) -> Iterator[None]:
    """Hold an exclusive lock shared by every socialmgr process using this data folder."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / LOCK_FILE, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def find_credential(session: Session, account_id: int) -> Credential | None:
    return session.scalar(
        select(Credential)
        .where(Credential.account_id == account_id, Credential.provider == PROVIDER)
        .order_by(Credential.id.desc())
        .limit(1)
    )


def save_tokens(
    session: Session, workspace_id: int, account_id: int, tokens: TokenSet, now: datetime
) -> Credential:
    """Insert or overwrite the account's X credentials (the caller commits)."""
    cred = find_credential(session, account_id)
    if cred is None:
        cred = Credential(workspace_id=workspace_id, account_id=account_id, provider=PROVIDER)
        session.add(cred)
    cred.access_token = tokens.access_token
    cred.refresh_token = tokens.refresh_token
    cred.expires_at = tokens.expires_at
    cred.refreshed_at = now
    session.flush()
    return cred


def needs_refresh(cred: Credential, now: datetime) -> bool:
    return cred.expires_at is None or cred.expires_at - now <= REFRESH_MARGIN


def fresh_access_token(
    settings: Settings,
    factory: sessionmaker[Session],
    http: httpx.Client,
    account_id: int,
    now: datetime,
) -> str:
    """Return a usable access token, refreshing (and committing the rotation) first if needed."""
    with token_lock(settings.data_dir):
        with factory() as session:
            cred = find_credential(session, account_id)
            if cred is None or not cred.access_token:
                raise ReauthRequired("No X credentials are stored for this account.")
            if not needs_refresh(cred, now):
                return cred.access_token
            if not cred.refresh_token:
                raise ReauthRequired("The X access token has expired and there is no refresh token.")
        return _refresh_locked(settings, factory, http, account_id, now)


def _refresh_locked(
    settings: Settings,
    factory: sessionmaker[Session],
    http: httpx.Client,
    account_id: int,
    now: datetime,
) -> str:
    with factory() as session:
        # Claim the database write lock first: once X has rotated the token we must be able to store it.
        try:
            session.execute(
                update(Credential)
                .where(Credential.account_id == account_id, Credential.provider == PROVIDER)
                .values(refreshed_at=Credential.refreshed_at)
            )
        except OperationalError as exc:
            raise TokenStoreBusy(
                "The database is busy, so the X token was not refreshed; try again shortly."
            ) from exc
        cred = find_credential(session, account_id)
        if cred is None or not cred.refresh_token:
            raise ReauthRequired("No X refresh token is stored for this account.")
        try:
            tokens = refresh_tokens(http, settings.x, cred.refresh_token, now)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise XUnavailable(f"Could not reach X to refresh the token: {exc}") from exc
        except OAuthError as exc:
            raise ReauthRequired(f"X refused to refresh the token ({exc.status}).") from exc
        except httpx.HTTPError as exc:
            # The request may have reached X and consumed the single-use token.
            raise ReauthRequired(f"The token refresh failed mid-request ({exc!r}).") from exc
        cred.access_token = tokens.access_token
        cred.refresh_token = tokens.refresh_token
        cred.expires_at = tokens.expires_at
        cred.refreshed_at = now
        session.commit()
        return tokens.access_token
