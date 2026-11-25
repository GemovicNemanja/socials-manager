"""``socialmgr connect`` with an injected browser and listener, plus the real one-shot listener."""

import socket
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from socialmgr import cli
from socialmgr.core.config import Settings
from socialmgr.db.models import Account, Credential
from socialmgr.providers.x.auth import s256_challenge
from socialmgr.providers.x.connect import local_listener, run_connect
from socialmgr.providers.x.errors import ConnectFailed
from tests.x_fakes import NOW, OWN_ID, FakeX, me_body, respond, seed_owner, x_settings

TOKENS = {"token_type": "bearer", "expires_in": 7200, "access_token": "acc", "refresh_token": "ref"}


class ScriptedBrowser:
    """Plays the member: reads the authorize URL and 'redirects' with a code and some state."""

    def __init__(self, state_override: str | None = None, extra: dict[str, str] | None = None) -> None:
        self.url: str | None = None
        self.state_override = state_override
        self.extra = extra or {}

    def open(self, url: str) -> bool:
        self.url = url
        return True

    @property
    def query(self) -> dict[str, str]:
        assert self.url is not None
        return {k: v[0] for k, v in parse_qs(urlsplit(self.url).query).items()}

    def callback_params(self) -> dict[str, str]:
        state = self.state_override if self.state_override is not None else self.query["state"]
        return {"code": "auth-code", "state": state, **self.extra}


class FakeListener:
    def __init__(self, browser: ScriptedBrowser) -> None:
        self.browser = browser
        self.bound: tuple[str, int, str] | None = None

    @contextmanager
    def __call__(self, host: str, port: int, path: str) -> Iterator["FakeListener"]:
        self.bound = (host, port, path)
        yield self

    def wait(self, timeout_s: float) -> dict[str, str]:
        assert self.browser.url is not None, "the browser must be opened while the listener is bound"
        return self.browser.callback_params()


def _connect(
    settings: Settings, factory: sessionmaker[Session], fake: FakeX, browser: ScriptedBrowser
) -> FakeListener:
    listener = FakeListener(browser)
    run_connect(
        settings,
        factory,
        http=fake.client(),
        open_browser=browser.open,
        listener=listener,
        echo=lambda _msg: None,
        clock=lambda: NOW,
    )
    return listener


def _api(me_username: str = "owner") -> FakeX:
    return (
        FakeX()
        .on("/2/oauth2/token", respond(TOKENS))
        .on("/2/users/me", respond(me_body(username=me_username)))
    )


def _credentials(factory: sessionmaker[Session]) -> list[Credential]:
    with factory() as session:
        return list(session.scalars(select(Credential)))


def test_connect_stores_tokens_and_external_id(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings, handle="@Owner")  # case and @ differ from the X username "owner"
    fake, browser = _api(), ScriptedBrowser()
    listener = _connect(s, factory, fake, browser)

    assert listener.bound == ("127.0.0.1", 8765, "/auth/x/callback")
    assert fake.paths() == ["/2/oauth2/token", "/2/users/me"]  # exchange first: the code lives ~30 s
    body = {k: v[0] for k, v in parse_qs(fake.calls[0].content.decode()).items()}
    assert body["code"] == "auth-code"
    assert s256_challenge(body["code_verifier"]) == browser.query["code_challenge"]
    assert fake.calls[1].headers["authorization"] == "Bearer acc"

    (cred,) = _credentials(factory)
    assert (cred.provider, cred.access_token, cred.refresh_token) == ("x", "acc", "ref")
    assert cred.expires_at is not None and cred.refreshed_at == NOW
    with factory() as session:
        owner = session.scalar(select(Account).where(Account.role == "owner"))
        assert owner is not None and owner.external_id == OWN_ID and owner.id == cred.account_id


def test_reconnect_overwrites_credentials(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    seed_owner(factory, access="old", refresh="old-r")
    _connect(s, factory, _api(), ScriptedBrowser())
    (cred,) = _credentials(factory)
    assert (cred.access_token, cred.refresh_token) == ("acc", "ref")


def test_state_mismatch_rejected(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    fake = _api()
    with pytest.raises(ConnectFailed, match="state"):
        _connect(s, factory, fake, ScriptedBrowser(state_override="forged"))
    assert fake.calls == []
    assert _credentials(factory) == []


def test_denied_consent(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    fake = _api()
    with pytest.raises(ConnectFailed, match="did not authorize"):
        _connect(s, factory, fake, ScriptedBrowser(extra={"error": "access_denied"}))
    assert fake.calls == []


def test_handle_mismatch_refused(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings, handle="@owner")
    with pytest.raises(ConnectFailed, match=r"@someone_else.*Nothing was stored"):
        _connect(s, factory, _api(me_username="someone_else"), ScriptedBrowser())
    assert _credentials(factory) == []
    with factory() as session:
        assert session.scalar(select(Account)) is None


def test_owner_linked_to_another_user_refused(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    seed_owner(factory, external_id="999", access=None)
    with pytest.raises(ConnectFailed, match="already linked"):
        _connect(s, factory, _api(), ScriptedBrowser())
    assert _credentials(factory) == []


def test_missing_client_id(settings: Settings, factory: sessionmaker[Session]) -> None:
    with pytest.raises(ConnectFailed, match="SOCIALMGR_X__CLIENT_ID"):
        _connect(settings, factory, _api(), ScriptedBrowser())


def test_failed_exchange(settings: Settings, factory: sessionmaker[Session]) -> None:
    s = x_settings(settings)
    fake = FakeX().on("/2/oauth2/token", respond({"error": "invalid_grant"}, status=400))
    with pytest.raises(ConnectFailed, match="exchange"):
        _connect(s, factory, fake, ScriptedBrowser())
    assert _credentials(factory) == []


# ---------------------------------------------------------------- the real listener (localhost only)


def test_local_listener_receives_one_redirect() -> None:
    with local_listener("127.0.0.1", 0, "/auth/x/callback") as listener:
        port = listener.port

        def browser() -> None:
            for path in ("/favicon.ico", "/auth/x/callback?code=abc&state=xyz"):
                with suppress(urllib.error.HTTPError):
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).read()

        t = threading.Thread(target=browser)
        t.start()
        params = listener.wait(timeout_s=10)
        t.join(timeout=5)
    assert params == {"code": "abc", "state": "xyz"}


def test_local_listener_times_out() -> None:
    with (
        local_listener("127.0.0.1", 0, "/cb") as listener,
        pytest.raises(ConnectFailed, match="No authorization"),
    ):
        listener.wait(timeout_s=0.2)


def test_busy_port_says_stop_serve() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        port = blocker.getsockname()[1]
        with (
            pytest.raises(ConnectFailed, match="stop `socialmgr serve` first"),
            local_listener("127.0.0.1", port, "/cb"),
        ):
            pass
    finally:
        blocker.close()


# ---------------------------------------------------------------- CLI


@pytest.mark.parametrize(
    ("command", "message"), [("connect", "SOCIALMGR_X__CLIENT_ID"), ("probe", "socialmgr connect")]
)
def test_cli_reports_setup_errors(
    command: str, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    env = {"SOCIALMGR_DATA_DIR": str(tmp_path / "data"), "SOCIALMGR_X__CLIENT_ID": ""}
    result = CliRunner().invoke(cli.app, [command], env=env)
    assert result.exit_code == 1
    assert message in result.output
