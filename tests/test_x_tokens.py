"""Locked token refresh against a real SQLite file: rotation is stored before any other call."""

import sqlite3
import threading
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.core.types import AccountRef, CollectionRequest
from socialmgr.db.session import session_factory
from socialmgr.providers.x.errors import ReauthRequired, TokenStoreBusy, XUnavailable
from socialmgr.providers.x.provider import DbTokenSource, XOwnerProvider
from socialmgr.providers.x.tokens import find_credential, fresh_access_token, token_lock
from tests.x_fakes import NOW, OWN_ID, FakeX, me_body, respond, seed_owner, x_settings

ROTATED = {
    "token_type": "bearer",
    "expires_in": 7200,
    "access_token": "access-2",
    "refresh_token": "refresh-2",
}


def _stored(factory: sessionmaker[Session], account_id: int) -> tuple[str | None, str | None]:
    with factory() as session:
        cred = find_credential(session, account_id)
        assert cred is not None
        return cred.access_token, cred.refresh_token


def test_valid_token_is_used_without_refresh(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, expires_in=timedelta(minutes=30))
    fake = FakeX()  # any request fails the test
    assert fresh_access_token(s, factory, fake.client(), acct, NOW) == "access-1"
    assert fake.calls == []


def test_refresh_rotates_and_persists_before_next_call(
    settings: Settings, factory: sessionmaker[Session]
) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, expires_in=timedelta(minutes=4))  # inside the 5-minute margin
    seen_in_db_at_first_api_call: list[tuple[str | None, str | None]] = []

    def users_me(request: httpx.Request) -> httpx.Response:
        if not seen_in_db_at_first_api_call:
            seen_in_db_at_first_api_call.append(_stored(factory, acct))
        assert request.headers["authorization"] == "Bearer access-2"
        return respond(me_body())(request)

    fake = (
        FakeX()
        .on("/2/oauth2/token", respond(ROTATED))
        .on("/2/users/me", users_me)
        .on(f"/2/users/{OWN_ID}/tweets", respond({"meta": {"result_count": 0}}))
        .on(f"/2/users/{OWN_ID}/followers", respond({"meta": {"result_count": 0}}))
    )
    http = fake.client()
    provider = XOwnerProvider(s, http=http, tokens=DbTokenSource(s, http, factory, clock=lambda: NOW))
    ref = AccountRef(account_id=acct, platform="x", handle="@Owner", role="owner", external_id=OWN_ID)
    provider.collect(CollectionRequest(accounts=(ref,), since=None, until=NOW))

    assert fake.paths()[0] == "/2/oauth2/token"
    assert seen_in_db_at_first_api_call == [("access-2", "refresh-2")]
    with factory() as session:
        cred = find_credential(session, acct)
        assert cred is not None
        assert cred.expires_at == NOW + timedelta(seconds=7200)
        assert cred.refreshed_at == NOW


def test_second_use_after_refresh_uses_new_token(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, expires_in=timedelta(minutes=1))
    fake = FakeX().on("/2/oauth2/token", respond(ROTATED))
    assert fresh_access_token(s, factory, fake.client(), acct, NOW) == "access-2"
    assert fresh_access_token(s, factory, fake.client(), acct, NOW) == "access-2"
    assert fake.paths() == ["/2/oauth2/token"]  # the single-use refresh token was spent once


def test_failed_refresh_raises_reauth_required(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, expires_in=timedelta(minutes=-10))
    fake = FakeX().on("/2/oauth2/token", respond({"error": "invalid_grant"}, status=400))
    with pytest.raises(ReauthRequired, match="socialmgr connect"):
        fresh_access_token(s, factory, fake.client(), acct, NOW)
    assert _stored(factory, acct) == ("access-1", "refresh-1")


def test_missing_credentials_raise_reauth_required(
    settings: Settings, factory: sessionmaker[Session]
) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, access=None)
    with pytest.raises(ReauthRequired, match="socialmgr connect"):
        fresh_access_token(s, factory, FakeX().client(), acct, NOW)


def test_expired_without_refresh_token(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, refresh=None, expires_in=timedelta(minutes=-1))
    with pytest.raises(ReauthRequired):
        fresh_access_token(s, factory, FakeX().client(), acct, NOW)


def test_unreachable_x_keeps_tokens(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, expires_in=timedelta(minutes=1))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    fake = FakeX().on("/2/oauth2/token", down)
    with pytest.raises(XUnavailable):
        fresh_access_token(s, factory, fake.client(), acct, NOW)
    assert _stored(factory, acct) == ("access-1", "refresh-1")


def test_busy_database_refuses_to_spend_refresh_token(
    settings: Settings, factory: sessionmaker[Session]
) -> None:
    s = x_settings(settings)
    acct = seed_owner(factory, expires_in=timedelta(minutes=1))
    impatient = session_factory(create_engine(s.db_url, connect_args={"timeout": 0.1}))
    holder = sqlite3.connect(s.db_path, timeout=0.1)
    holder.execute("BEGIN IMMEDIATE")  # another writer (e.g. a run's open transaction)
    try:
        fake = FakeX().on("/2/oauth2/token", respond(ROTATED))
        with pytest.raises(TokenStoreBusy):
            fresh_access_token(s, impatient, fake.client(), acct, NOW)
        assert fake.calls == []
    finally:
        holder.rollback()
        holder.close()
    assert _stored(factory, acct) == ("access-1", "refresh-1")


def test_token_lock_is_exclusive(settings: Settings) -> None:
    acquired = threading.Event()

    def contender() -> None:
        with token_lock(settings.data_dir):
            acquired.set()

    with token_lock(settings.data_dir):
        t = threading.Thread(target=contender)
        t.start()
        time.sleep(0.2)
        assert not acquired.is_set()
    t.join(timeout=5)
    assert acquired.is_set()
