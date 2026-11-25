"""OAuth 2.0 PKCE for a public client: challenge, authorize URL, callback checks, token requests."""

import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from socialmgr.core.config import XConfig
from socialmgr.providers.x.auth import (
    authorize_url,
    code_from_callback,
    exchange_code,
    new_pkce,
    refresh_tokens,
    s256_challenge,
)
from socialmgr.providers.x.errors import ConnectFailed, OAuthError
from tests.x_fakes import NOW, FakeX, respond

CFG = XConfig(client_id="test-client")
TOKEN_BODY = {
    "token_type": "bearer",
    "expires_in": 7200,
    "access_token": "access-new",
    "refresh_token": "refresh-new",
    "scope": "tweet.read users.read follows.read offline.access",
}


def test_s256_matches_rfc7636_example() -> None:
    # RFC 7636 Appendix B.
    assert (
        s256_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_new_pkce_is_valid_and_random() -> None:
    a, b = new_pkce(), new_pkce()
    assert a.verifier != b.verifier
    assert 43 <= len(a.verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", a.verifier)
    assert a.challenge == s256_challenge(a.verifier)
    assert "=" not in a.challenge


def test_authorize_url_params() -> None:
    url = authorize_url(CFG, state="st4te", challenge="ch4llenge")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://x.com/i/oauth2/authorize"
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q == {
        "response_type": "code",
        "client_id": "test-client",
        "redirect_uri": "http://127.0.0.1:8765/auth/x/callback",
        "scope": "tweet.read users.read follows.read offline.access",
        "state": "st4te",
        "code_challenge": "ch4llenge",
        "code_challenge_method": "S256",
    }


def test_authorize_url_requires_client_id() -> None:
    with pytest.raises(ConnectFailed):
        authorize_url(XConfig(), state="s", challenge="c")


def test_callback_state_mismatch_rejected() -> None:
    with pytest.raises(ConnectFailed, match="state"):
        code_from_callback({"code": "abc", "state": "other"}, expected_state="mine")
    with pytest.raises(ConnectFailed, match="state"):
        code_from_callback({"code": "abc"}, expected_state="mine")


def test_callback_error_and_missing_code() -> None:
    with pytest.raises(ConnectFailed, match="did not authorize"):
        code_from_callback({"error": "access_denied", "state": "s"}, expected_state="s")
    with pytest.raises(ConnectFailed, match="no authorization code"):
        code_from_callback({"state": "s"}, expected_state="s")
    assert code_from_callback({"code": "abc", "state": "s"}, expected_state="s") == "abc"


def test_code_exchange_body() -> None:
    fake = FakeX().on("/2/oauth2/token", respond(TOKEN_BODY))
    tokens = exchange_code(fake.client(), CFG, code="the-code", verifier="the-verifier", now=NOW)
    (req,) = fake.calls
    assert req.method == "POST"
    assert str(req.url) == "https://api.x.com/2/oauth2/token"
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert "authorization" not in req.headers  # public client: no Basic auth
    assert {k: v[0] for k, v in parse_qs(req.content.decode()).items()} == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": "http://127.0.0.1:8765/auth/x/callback",
        "code_verifier": "the-verifier",
        "client_id": "test-client",
    }
    assert tokens.access_token == "access-new"
    assert tokens.refresh_token == "refresh-new"
    assert tokens.expires_at == NOW + timedelta(seconds=7200)


def test_refresh_body() -> None:
    fake = FakeX().on("/2/oauth2/token", respond(TOKEN_BODY))
    refresh_tokens(fake.client(), CFG, "refresh-old", NOW)
    (req,) = fake.calls
    assert "authorization" not in req.headers
    assert {k: v[0] for k, v in parse_qs(req.content.decode()).items()} == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-old",
        "client_id": "test-client",
    }


def test_token_error_raises() -> None:
    fake = FakeX().on("/2/oauth2/token", respond({"error": "invalid_request"}, status=400))
    with pytest.raises(OAuthError) as exc:
        exchange_code(fake.client(), CFG, code="c", verifier="v", now=NOW)
    assert exc.value.status == 400


def test_mock_transport_only() -> None:
    # Guard: the fake refuses anything it wasn't scripted for, so a stray real endpoint fails loudly.
    fake = FakeX()
    with pytest.raises(AssertionError):
        fake.client().get("https://api.x.com/2/users/me")
    assert isinstance(fake.client(), httpx.Client)
