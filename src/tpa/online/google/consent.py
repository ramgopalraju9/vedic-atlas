"""Google OAuth consent helpers (used once, by scripts/google_auth.py): the authorisation URL, PKCE, the code
exchange and saving the refresh token into .env. Pure except `exchange_code`, which goes through the allow-listed client.

Scopes are the least that the tools need: read mail, create/send drafts, read and add calendar events. Veda never calls
any delete endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from exceptions.exception import ToolUnavailableError
from tpa.online.google.google_auth import TOKEN_URL
from tpa.online.http_client import AllowListedHttpClient

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
)


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    # access_type=offline + prompt=consent: without both Google may not return a refresh token at all.
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    })


async def exchange_code(
    http: AllowListedHttpClient, *, client_id: str, client_secret: str, code: str, verifier: str, redirect_uri: str,
) -> dict:
    return await http.post_form(
        TOKEN_URL, category="google",
        data={
            "client_id": client_id, "client_secret": client_secret, "code": code, "code_verifier": verifier,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        },
    )


def refresh_token_from(payload: dict) -> str:
    """The refresh token of a token response, only if every scope the tools need was granted."""
    granted = set(str(payload.get("scope") or "").split())
    missing = [s.rsplit("/", 1)[-1] for s in SCOPES if s not in granted]
    if missing:
        raise ToolUnavailableError("google_consent", "google", f"these permissions were not granted: {', '.join(missing)}")
    token = str(payload.get("refresh_token") or "")
    if not token:
        raise ToolUnavailableError("google_consent", "google", "Google returned no refresh token (revoke Veda in your Google account and retry)")
    return token


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, keeping every other line byte-for-byte; written atomically."""
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
    out: list[str] = []
    done = False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and "=" in line:
            if not done:
                out.append(f"{key}={value}\n")
                done = True
            continue   # a duplicate of the key is dropped
        out.append(line)
    if not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={value}\n")
    fd, tmp = tempfile.mkstemp(dir=str(env_path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write("".join(out))
        os.replace(tmp, env_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
