"""Google sign-in for Gmail + Calendar (+ reminders), run from the terminal: `veda login`, or offered when Veda starts.

Two ways to give consent, chosen automatically (override with --browser / --manual):

  browser  A tiny loopback server catches the redirect. Needs a browser on THIS machine (a laptop).
  manual   For a headless Raspberry Pi over SSH: print the sign-in URL, open it on ANY device (phone, laptop), allow access,
           the browser then fails to load http://127.0.0.1:<port>/?code=...  That is expected: copy the whole address from
           the address bar and paste it here. Nothing listens on the Pi, so no tunnel is needed.

Google's easier "enter a code on your phone" flow is not an option: it does not permit the Gmail or Calendar scopes.

Needs GOOGLE_API_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env. The refresh token is written to .env and never printed.
A "Desktop app" OAuth client accepts any loopback port; a "Web application" client needs `http://127.0.0.1:<port>` registered
(use --port with that port; manual mode defaults to 8765).
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from core.constants import PROJECT_ROOT
from core.env import load_env
from exceptions.exception import AppException
from tpa.online.google.consent import (
    SCOPES, build_auth_url, exchange_code, pkce_pair, refresh_token_from, upsert_env_value,
)
from tpa.online.google.google_auth import TOKEN_HOST, GoogleAuth
from tpa.online.http_client import AllowListedHttpClient

WAIT_SEC = 240
MANUAL_PORT = 8765
_DONE = b"<html><body style='font-family:sans-serif'><h3>Veda is linked to your Google account.</h3>You can close this tab.</body></html>"
_FAILED = b"<html><body style='font-family:sans-serif'><h3>Sign-in did not complete.</h3>Return to the terminal.</body></html>"

Say = Callable[[str], None]


def _http() -> AllowListedHttpClient:
    return AllowListedHttpClient(allow_list=frozenset({TOKEN_HOST}), timeout=20.0)


def make_auth(http: AllowListedHttpClient) -> GoogleAuth:
    return GoogleAuth(
        http,
        client_id=lambda: os.environ.get("GOOGLE_API_CLIENT_ID"),
        client_secret=lambda: os.environ.get("GOOGLE_CLIENT_SECRET"),
        refresh_token=lambda: os.environ.get("GOOGLE_REFRESH_TOKEN"),
        required_scopes=SCOPES,
    )


def can_open_browser() -> bool:
    """True where a browser can plausibly open on this machine; False on a headless Linux box (a Pi over SSH)."""
    if sys.platform in ("win32", "darwin"):
        return True
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        webbrowser.get()
        return True
    except webbrowser.Error:
        return False


def parse_redirect(pasted: str, state: str) -> str:
    """The authorisation code from what the user pasted: the full redirect address, or just the code.

    ValueError (with a sentence to show) when it is an error redirect, has the wrong `state` (not our request), or
    holds no code."""
    text = (pasted or "").strip().strip("'\"<>")
    if not text:
        raise ValueError("nothing was pasted")
    if "=" not in text and "?" not in text:
        return text   # the bare code
    query = parse_qs(urlparse(text).query if "?" in text else text)
    if "error" in query:
        raise ValueError(f"Google said: {query['error'][0]}")
    if query.get("state", [state])[0] != state:
        raise ValueError("that address belongs to a different sign-in attempt; start again")
    if "code" not in query:
        raise ValueError("no code in that address; copy the whole address after allowing access")
    return query["code"][0]


def _make_handler(state: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            ok = query.get("state", [""])[0] == state and "code" in query
            self.server.captured = {"code": query["code"][0]} if ok else {"error": query.get("error", ["bad state"])[0]}  # type: ignore[attr-defined]
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_DONE if ok else _FAILED)

        def log_message(self, *_):   # never log the request line: it carries the code
            pass

    return Handler


async def check(say: Say = print) -> int:
    auth = make_auth(_http())
    status = await auth.status()
    if status["state"] == "ok":
        say("OK - Google sign-in works.")
        return 0
    detail = {
        "not_linked": "not linked yet (missing: " + ", ".join(status.get("missing_secrets", [])) + ")",
        "rejected": "Google refused the saved sign-in",
        "needs_permission": "the saved sign-in lacks: " + ", ".join(status.get("missing_scopes", [])),
        "unreachable": "could not reach Google",
    }.get(status["state"], status["state"])
    say(f"Sign-in needs attention - {detail}. Run: veda login")
    return 1


async def link(*, manual: bool | None = None, port: int = 0, say: Say = print, ask: Callable[[str], str] = input) -> int:
    """Sign in and save GOOGLE_REFRESH_TOKEN to .env. 0 on success."""
    load_env()
    auth = make_auth(_http())
    absent = [n for n in auth.missing() if n != "refresh token"]
    if absent:
        say(f"Add GOOGLE_API_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env first (missing: {', '.join(absent)}).")
        return 1
    client_id = os.environ["GOOGLE_API_CLIENT_ID"].strip()
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"].strip()
    if manual is None:
        manual = not can_open_browser()

    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    server = None
    if manual:
        redirect_uri = f"http://127.0.0.1:{port or MANUAL_PORT}"
    else:
        server = HTTPServer(("127.0.0.1", port), _make_handler(state))
        server.timeout = WAIT_SEC
        redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = build_auth_url(client_id, redirect_uri, state, challenge)
    say(f"Redirect URI used: {redirect_uri}")

    try:
        if manual:
            say(
                "\n1. Open this address on any device (phone or laptop) and allow access:\n\n" + url + "\n\n"
                "2. The page that opens afterwards will NOT load (\"can't reach 127.0.0.1\"). That is expected.\n"
                "3. Copy the whole address from the browser's address bar and paste it below.\n"
            )
            try:
                code = parse_redirect(await asyncio.to_thread(ask, "Paste the address here: "), state)
            except ValueError as e:
                say(f"Sign-in did not complete: {e}.")
                return 1
        else:
            say("Opening your browser to sign in. If it does not open, paste this address into a browser:\n\n" + url + "\n")
            webbrowser.open(url)
            await asyncio.to_thread(server.handle_request)
            captured = getattr(server, "captured", {})
            if "code" not in captured:
                say(f"Sign-in did not complete ({captured.get('error', 'no valid response within %d s' % WAIT_SEC)}).")
                return 1
            code = captured["code"]
    finally:
        if server is not None:
            server.server_close()

    try:
        payload = await exchange_code(
            _http(), client_id=client_id, client_secret=client_secret, code=code, verifier=verifier, redirect_uri=redirect_uri,
        )
        token = refresh_token_from(payload)
    except AppException as e:
        say(f"Could not finish linking: {e.message}")
        return 1
    upsert_env_value(PROJECT_ROOT / ".env", "GOOGLE_REFRESH_TOKEN", token)
    os.environ["GOOGLE_REFRESH_TOKEN"] = token
    say("Linked. The sign-in was saved to .env (not shown).")
    return 0


def run_link(**kw) -> int:
    return asyncio.run(link(**kw))


def run_check(**kw) -> int:
    return asyncio.run(check(**kw))
