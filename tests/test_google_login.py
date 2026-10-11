"""Google sign-in at startup: status states, the headless paste-the-address flow, token reload, and the API."""

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from controller.cli import google_login as gl
from exceptions.exception import ToolUnavailableError
from tpa.online.google.consent import SCOPES
from tpa.online.google.google_auth import GoogleAuth

CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"
CAL_READONLY = "https://www.googleapis.com/auth/calendar.readonly"


def run(coro):
    return asyncio.run(coro)


class Http:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    async def post_form(self, url, *, category, data, headers=None):
        if self.error:
            raise self.error
        return self.payload


def auth_with(http, secrets=("id", "secret", "refresh")):
    cid, sec, ref = secrets
    return GoogleAuth(http, client_id=lambda: cid, client_secret=lambda: sec, refresh_token=lambda: ref, required_scopes=SCOPES)


def token(scopes):
    return {"access_token": "T", "expires_in": 3600, "scope": " ".join(scopes)}


# ---- status ----------------------------------------------------------------------------------------------------------

def test_status_ok_when_every_required_permission_is_granted():
    assert run(auth_with(Http(token(SCOPES))).status())["state"] == "ok"


def test_status_says_exactly_which_permission_is_missing_for_an_old_read_only_sign_in():
    old = [s for s in SCOPES if s != CAL_EVENTS] + [CAL_READONLY]
    status = run(auth_with(Http(token(old))).status())
    assert status == {"state": "needs_permission", "missing_secrets": [], "missing_scopes": ["calendar.events"]}


def test_the_full_calendar_scope_also_covers_event_access():
    broad = [s for s in SCOPES if s != CAL_EVENTS] + ["https://www.googleapis.com/auth/calendar"]
    assert run(auth_with(Http(token(broad))).status())["state"] == "ok"


def test_status_when_google_does_not_say_what_the_token_carries_is_ok():
    assert run(auth_with(Http({"access_token": "T", "expires_in": 3600})).status())["state"] == "ok"


def test_status_not_linked_names_the_missing_secret_by_role_only():
    status = run(auth_with(Http(), secrets=("id", "secret", "")).status())
    assert status["state"] == "not_linked" and status["missing_secrets"] == ["refresh token"]
    assert "secret" not in str({k: v for k, v in status.items() if k != "missing_secrets"})


def test_status_rejected_vs_unreachable():
    refused = ToolUnavailableError("GoogleAuth", "google", "refused", status=400, not_configured=True)
    assert run(auth_with(Http(error=refused)).status())["state"] == "rejected"
    offline = ToolUnavailableError("GoogleAuth", "google", "timed out")
    assert run(auth_with(Http(error=offline)).status())["state"] == "unreachable"


def test_reset_forgets_the_cached_token_and_granted_scopes():
    a = auth_with(Http(token(SCOPES)))
    run(a.status())
    a.reset()
    assert a._token == "" and a._granted == frozenset()


# ---- pasting the redirect --------------------------------------------------------------------------------------------

def test_parse_redirect_accepts_the_full_address_or_just_the_code():
    assert gl.parse_redirect("http://127.0.0.1:8765/?state=S&code=4%2Fabc&scope=x", "S") == "4/abc"
    assert gl.parse_redirect("  '4/abc' ", "S") == "4/abc"
    assert gl.parse_redirect("code=4/abc&state=S", "S") == "4/abc"


@pytest.mark.parametrize("pasted,why", [
    ("", "nothing was pasted"),
    ("http://127.0.0.1:8765/?error=access_denied&state=S", "access_denied"),
    ("http://127.0.0.1:8765/?state=OTHER&code=x", "different sign-in attempt"),
    ("http://127.0.0.1:8765/?state=S", "no code"),
])
def test_parse_redirect_refuses_bad_input_with_a_sentence(pasted, why):
    with pytest.raises(ValueError, match=why):
        gl.parse_redirect(pasted, "S")


def test_a_headless_linux_box_chooses_the_paste_flow(monkeypatch):
    monkeypatch.setattr(gl.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert gl.can_open_browser() is False
    monkeypatch.setattr(gl.sys, "platform", "win32")
    assert gl.can_open_browser() is True


# ---- the whole headless sign-in, end to end (no browser, no network) ------------------------------------------------

def test_manual_sign_in_saves_the_token_and_never_prints_it(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_API_CLIENT_ID=cid\nGOOGLE_CLIENT_SECRET=csec\nGOOGLE_REFRESH_TOKEN=\nOTHER=keep\n")
    monkeypatch.setenv("GOOGLE_API_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(gl, "load_env", lambda: None)
    seen = {}

    async def fake_exchange(http, *, client_id, client_secret, code, verifier, redirect_uri):
        seen.update(code=code, redirect_uri=redirect_uri, verifier=verifier)
        return {"refresh_token": "SECRET-REFRESH", "scope": " ".join(SCOPES)}

    monkeypatch.setattr(gl, "exchange_code", fake_exchange)
    said: list[str] = []

    def ask(prompt):
        url = next(line for line in " ".join(said).split() if line.startswith("https://accounts.google.com"))
        query = parse_qs(urlparse(url).query)
        assert query["redirect_uri"] == ["http://127.0.0.1:8765"] and "calendar.events" in query["scope"][0]
        return f"http://127.0.0.1:8765/?state={query['state'][0]}&code=THE-CODE&scope=x"

    rc = gl.run_link(manual=True, say=said.append, ask=ask)
    assert rc == 0 and seen["code"] == "THE-CODE" and seen["redirect_uri"] == "http://127.0.0.1:8765"
    env = (tmp_path / ".env").read_text()
    assert "GOOGLE_REFRESH_TOKEN=SECRET-REFRESH" in env and "OTHER=keep" in env
    assert "SECRET-REFRESH" not in " ".join(said)


def test_manual_sign_in_with_a_wrong_paste_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_REFRESH_TOKEN=\n")
    monkeypatch.setenv("GOOGLE_API_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
    monkeypatch.setattr(gl, "load_env", lambda: None)
    said: list[str] = []
    rc = gl.run_link(manual=True, say=said.append, ask=lambda _p: "http://127.0.0.1:8765/?error=access_denied")
    assert rc == 1 and (tmp_path / ".env").read_text() == "GOOGLE_REFRESH_TOKEN=\n" and "access_denied" in " ".join(said)


def test_sign_in_without_client_id_and_secret_says_what_to_add(monkeypatch):
    for k in ("GOOGLE_API_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(gl, "load_env", lambda: None)
    said: list[str] = []
    assert gl.run_link(manual=True, say=said.append) == 1 and "GOOGLE_API_CLIENT_ID" in said[0]


# ---- reload + API ---------------------------------------------------------------------------------------------------

def test_reload_env_replaces_only_google_entries(tmp_path, monkeypatch):
    from core import env

    monkeypatch.setattr(env, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_REFRESH_TOKEN=NEW\nTAVILY_API_KEY=other\n")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "OLD")
    monkeypatch.setenv("TAVILY_API_KEY", "keepme")
    assert env.reload_env("GOOGLE_") == ["GOOGLE_REFRESH_TOKEN"]
    import os
    assert os.environ["GOOGLE_REFRESH_TOKEN"] == "NEW" and os.environ["TAVILY_API_KEY"] == "keepme"


def test_api_status_and_reload(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from controller.routes import google as route

    monkeypatch.setattr(route, "reload_env", lambda prefix: [])   # never touch the real .env / environment in a test
    auth = auth_with(Http(token([s for s in SCOPES if s != CAL_EVENTS])))
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.state.google_auth = auth
    client = TestClient(app)
    assert client.get("/api/google/status").json()["state"] == "needs_permission"
    app.state.google_auth._http.payload = token(SCOPES)        # the user signed in again
    assert client.post("/api/google/reload").json()["state"] == "ok"
    assert "refresh" not in client.get("/api/google/status").text.lower()


def test_api_is_503_when_google_tools_are_off():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from controller.routes import google as route

    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    assert TestClient(app).get("/api/google/status").status_code == 503
