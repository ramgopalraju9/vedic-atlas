"""GoogleAuth — turns the OAuth refresh token in the environment into short-lived access tokens.

The three secrets are read at call time through injected callables (like the Tavily key): they are never stored in
config, logged, or kept anywhere but the process environment (.env, git-ignored). The refresh token itself comes from the
one-time consent in `scripts/google_auth.py`. Access tokens live ~1 h and are cached in memory only.

A missing secret, or Google refusing the refresh token (revoked, or expired after 7 days while the OAuth consent screen
is still in "Testing"), raises ToolUnavailableError(not_configured=True): the user hears "it isn't set up", not a stack trace.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from exceptions.exception import ToolUnavailableError
from tpa.online.http_client import AllowListedHttpClient

TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_URL = f"https://{TOKEN_HOST}/token"
_SCOPE_ROOT = "https://www.googleapis.com/auth"
_EARLY_SEC = 60   # refresh a minute before expiry so a request never carries a token that dies in flight


class GoogleAuth:
    def __init__(
        self,
        http_client: AllowListedHttpClient,
        *,
        client_id: Callable[[], str | None],
        client_secret: Callable[[], str | None],
        refresh_token: Callable[[], str | None],
        clock: Callable[[], float] = time.monotonic,
        required_scopes: tuple[str, ...] = (),
    ):
        self._http = http_client
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._clock = clock
        self._token = ""
        self._expires_at = 0.0
        self._granted: frozenset[str] = frozenset()   # scopes Google says the saved sign-in carries
        self._required = tuple(required_scopes)
        self._lock = asyncio.Lock()

    def missing(self) -> list[str]:
        """Which of the three secrets are absent (by role, never by value)."""
        pairs = (("client id", self._client_id), ("client secret", self._client_secret), ("refresh token", self._refresh_token))
        return [name for name, get in pairs if not (get() or "").strip()]

    def is_configured(self) -> bool:
        return not self.missing()

    async def access_token(self) -> str:
        async with self._lock:
            if self._token and self._clock() < self._expires_at - _EARLY_SEC:
                return self._token
            absent = self.missing()
            if absent:
                raise ToolUnavailableError(
                    "GoogleAuth", "google", f"Google is not set up (missing {', '.join(absent)}; run scripts/google_auth.py)",
                    not_configured=True,
                )
            try:
                payload = await self._http.post_form(
                    TOKEN_URL, category="google",
                    data={
                        "client_id": self._client_id().strip(), "client_secret": self._client_secret().strip(),
                        "refresh_token": self._refresh_token().strip(), "grant_type": "refresh_token",
                    },
                )
            except ToolUnavailableError as e:
                if e.status in (400, 401):   # invalid_grant / invalid_client: only a new consent fixes it
                    raise ToolUnavailableError(
                        "GoogleAuth", "google", "Google refused the saved sign-in (run scripts/google_auth.py again)",
                        status=e.status, not_configured=True,
                    ) from e
                raise
            token = str(payload.get("access_token") or "")
            if not token:
                raise ToolUnavailableError("GoogleAuth", "google", "Google returned no access token")
            self._token = token
            self._granted = frozenset(str(payload.get("scope") or "").split())
            self._expires_at = self._clock() + float(payload.get("expires_in") or 3600)
            return token

    def reset(self) -> None:
        """Forget the cached access token (the sign-in in .env was just replaced)."""
        self._token, self._expires_at, self._granted = "", 0.0, frozenset()

    def _lacking(self) -> list[str]:
        """Required scopes the saved sign-in does not carry (empty when Google did not say what it carries)."""
        if not self._granted:
            return []
        implied = {s for s in self._granted}
        if f"{_SCOPE_ROOT}/calendar" in implied:   # the full calendar scope includes event access
            implied.add(f"{_SCOPE_ROOT}/calendar.events")
        return [s for s in self._required if s not in implied]

    async def status(self) -> dict:
        """What the user needs to do, if anything: state is ok | not_linked | rejected | needs_permission | unreachable.
        `missing_secrets` names client id / secret / refresh token by role, never by value."""
        absent = self.missing()
        if absent:
            return {"state": "not_linked", "missing_secrets": absent, "missing_scopes": []}
        try:
            await self.access_token()
        except ToolUnavailableError as e:
            state = "rejected" if e.not_configured else "unreachable"
            return {"state": state, "missing_secrets": [], "missing_scopes": [], "detail": e.message}
        lacking = [s.rsplit("/", 1)[-1] for s in self._lacking()]
        return {"state": "needs_permission" if lacking else "ok", "missing_secrets": [], "missing_scopes": lacking}

    async def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.access_token()}"}
