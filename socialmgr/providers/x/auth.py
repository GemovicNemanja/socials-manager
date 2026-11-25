"""OAuth 2.0 Authorization Code with PKCE for a public client (X "Native App", no client secret).

Public clients send ``client_id`` in the form body and no Authorization header. X refresh tokens
are single-use: every refresh returns a new one that must be stored before anything else happens.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from socialmgr.core.config import XConfig
from socialmgr.providers.x.errors import ConnectFailed, OAuthError

TOKEN_PATH = "/2/oauth2/token"


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str


def s256_challenge(verifier: str) -> str:
    """RFC 7636 S256: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_pkce() -> Pkce:
    # 64 random bytes -> 86 unreserved characters (RFC 7636 allows 43-128).
    verifier = secrets.token_urlsafe(64)
    return Pkce(verifier=verifier, challenge=s256_challenge(verifier))


def new_state() -> str:
    return secrets.token_urlsafe(32)


def authorize_url(cfg: XConfig, state: str, challenge: str) -> str:
    if not cfg.client_id:
        raise ConnectFailed("x.client_id is not set")
    query = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{cfg.authorize_url}?{urlencode(query)}"


def code_from_callback(params: dict[str, str], expected_state: str) -> str:
    """Validate the redirect's query and return the authorization code."""
    if not secrets.compare_digest(params.get("state", ""), expected_state):
        raise ConnectFailed("The callback's state doesn't match this login attempt; nothing was stored.")
    if "error" in params:
        detail = params.get("error_description") or params["error"]
        raise ConnectFailed(f"X did not authorize the app: {detail}")
    code = params.get("code")
    if not code:
        raise ConnectFailed("The callback carried no authorization code.")
    return code


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scope: str | None


def _token_request(http: httpx.Client, cfg: XConfig, form: dict[str, str], now: datetime) -> TokenSet:
    response = http.post(
        f"{cfg.api_base}{TOKEN_PATH}",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code != 200:
        raise OAuthError(response.status_code, response.text)
    body = response.json()
    expires_in = body.get("expires_in")
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_at=now + timedelta(seconds=int(expires_in)) if expires_in is not None else None,
        scope=body.get("scope"),
    )


def exchange_code(http: httpx.Client, cfg: XConfig, code: str, verifier: str, now: datetime) -> TokenSet:
    """Trade the authorization code (valid ~30 s) for tokens."""
    assert cfg.client_id
    return _token_request(
        http,
        cfg,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "code_verifier": verifier,
            "client_id": cfg.client_id,
        },
        now,
    )


def refresh_tokens(http: httpx.Client, cfg: XConfig, refresh_token: str, now: datetime) -> TokenSet:
    """Use (and so consume) a single-use refresh token."""
    if not cfg.client_id:
        raise OAuthError(0, "x.client_id is not set")
    return _token_request(
        http,
        cfg,
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": cfg.client_id},
        now,
    )
