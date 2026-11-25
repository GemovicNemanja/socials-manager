"""``socialmgr connect``: link the owner's X account with OAuth 2.0 + PKCE.

The browser and the local callback listener are injected so tests run without either.
"""

import errno
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

import httpx
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.providers.x.api import RateLimited, XApi
from socialmgr.providers.x.auth import (
    authorize_url,
    code_from_callback,
    exchange_code,
    new_pkce,
    new_state,
)
from socialmgr.providers.x.errors import ConnectFailed, OAuthError
from socialmgr.providers.x.tokens import save_tokens, token_lock

CONNECT_TIMEOUT_S = 300.0
CALLBACK_PAGE = (
    b"<!doctype html><title>Social Manager</title>"
    b"<p>Received. You can close this tab and return to the terminal.</p>"
)


class CallbackListener(Protocol):
    def wait(self, timeout_s: float) -> dict[str, str]:
        """Block until the redirect arrives; return its query parameters."""
        ...


type ListenerFactory = Callable[[str, int, str], AbstractContextManager[CallbackListener]]


class _OneShotServer(HTTPServer):
    params: dict[str, str] | None = None
    callback_path: str = "/"


class _Handler(BaseHTTPRequestHandler):
    server: _OneShotServer

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path != self.server.callback_path:
            self.send_error(404)
            return
        self.server.params = dict(parse_qsl(url.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(CALLBACK_PAGE)))
        self.end_headers()
        self.wfile.write(CALLBACK_PAGE)

    def log_message(self, format: str, *args: object) -> None:  # keep the code out of the terminal
        return


class _LocalListener:
    def __init__(self, server: _OneShotServer) -> None:
        self.server = server

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def wait(self, timeout_s: float) -> dict[str, str]:
        deadline = monotonic() + timeout_s
        self.server.timeout = 0.5
        while self.server.params is None:
            if monotonic() >= deadline:
                raise ConnectFailed(
                    f"No authorization arrived within {timeout_s / 60:.0f} minutes; "
                    "run `socialmgr connect` again."
                )
            self.server.handle_request()
        return self.server.params


@contextmanager
def local_listener(host: str, port: int, path: str) -> Iterator[_LocalListener]:
    """A temporary HTTP listener for exactly one redirect. Binds before the browser opens."""
    try:
        server = _OneShotServer((host, port), _Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise ConnectFailed(
                f"Port {port} on {host} is in use: stop `socialmgr serve` first, then run "
                "`socialmgr connect` again."
            ) from exc
        raise
    server.callback_path = path
    try:
        yield _LocalListener(server)
    finally:
        server.server_close()


def normalize_handle(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


@dataclass(frozen=True)
class Connected:
    user_id: str
    username: str
    expires_at: datetime | None
    scope: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def run_connect(
    settings: Settings,
    factory: sessionmaker[Session],
    *,
    http: httpx.Client,
    open_browser: Callable[[str], object] = webbrowser.open,
    listener: ListenerFactory = local_listener,
    echo: Callable[[str], object] = print,
    clock: Callable[[], datetime] = _utcnow,
    timeout_s: float = CONNECT_TIMEOUT_S,
) -> Connected:
    """Authorize in the browser, check it's the configured account, and store the tokens."""
    from socialmgr.services.pipeline import ensure_accounts

    cfg = settings.x
    if not cfg.client_id:
        raise ConnectFailed(
            "x.client_id is not set. Add SOCIALMGR_X__CLIENT_ID=<your app's OAuth 2.0 Client ID> to .env "
            "(X developer portal: app type Native App, callback "
            f"{cfg.redirect_uri}) and run `socialmgr connect` again."
        )
    target = urlsplit(cfg.redirect_uri)
    if target.hostname is None or target.port is None:
        raise ConnectFailed(f"x.redirect_uri must name a host and port, got {cfg.redirect_uri!r}")

    pkce = new_pkce()
    state = new_state()
    url = authorize_url(cfg, state, pkce.challenge)
    with listener(target.hostname, target.port, target.path or "/") as callback:
        echo(f"Opening your browser to authorize Social Manager. If it doesn't open, visit:\n{url}")
        open_browser(url)
        params = callback.wait(timeout_s)

    code = code_from_callback(params, state)
    # The code expires about 30 s after the redirect: exchange it before anything else.
    try:
        tokens = exchange_code(http, cfg, code, pkce.verifier, clock())
    except OAuthError as exc:
        raise ConnectFailed(f"Could not exchange the authorization code: {exc}") from exc

    try:
        me = XApi(http, cfg.api_base, tokens.access_token).get("/2/users/me").body["data"]
    except RateLimited as exc:
        raise ConnectFailed(f"X rate-limited the account lookup; try again later ({exc}).") from exc
    user_id, username = str(me["id"]), str(me["username"])

    if normalize_handle(username) != normalize_handle(settings.account.handle):
        raise ConnectFailed(
            f"You authorized @{username}, but this install tracks {settings.account.handle}. Nothing was "
            "stored. Sign in to X as that account (or change SOCIALMGR_ACCOUNT__HANDLE) and run "
            "`socialmgr connect` again."
        )

    now = clock()
    with token_lock(settings.data_dir), factory() as session:
        owner = next(a for a in ensure_accounts(session, settings, now) if a.role == "owner")
        if owner.external_id is not None and owner.external_id != user_id:
            raise ConnectFailed(
                f"The owner account in {settings.data_dir} is already linked to X user id "
                f"{owner.external_id} (fixture data?), not @{username} ({user_id}). Nothing was stored; "
                "use a fresh SOCIALMGR_DATA_DIR."
            )
        owner.external_id = user_id
        save_tokens(session, settings.workspace_id, owner.id, tokens, now)
        session.commit()
    return Connected(user_id=user_id, username=username, expires_at=tokens.expires_at, scope=tokens.scope)
