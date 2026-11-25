"""Typed errors of the X integration. The CLI prints their message and exits non-zero."""


class XError(RuntimeError):
    """Base class for every error the X integration raises on purpose."""


class ReauthRequired(XError):
    """The stored credentials can't be used or refreshed; the member must connect again."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{reason} Run `socialmgr connect` to reconnect your X account.")
        self.reason = reason


class XUnavailable(XError):
    """X couldn't be reached before anything was sent; stored credentials are untouched."""


class TokenStoreBusy(XError):
    """The database was locked, so a refresh was not attempted (it would burn the refresh token)."""


class OAuthError(XError):
    """The token endpoint rejected a request."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"X token endpoint returned {status}: {body[:300]}")
        self.status = status
        self.body = body


class XApiError(XError):
    """An API call failed with a status the caller doesn't handle."""

    def __init__(self, status: int, path: str, body: str) -> None:
        super().__init__(f"X API {path} returned {status}: {body[:300]}")
        self.status = status
        self.path = path


class ConnectFailed(XError):
    """``socialmgr connect`` stopped: bad state, denied consent, wrong account, timeout, busy port."""
