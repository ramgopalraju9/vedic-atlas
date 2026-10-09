"""Google sign-in API — is mail/calendar access working, and pick up a fresh sign-in without restarting the server.

`GET  /google/status`  state is ok | not_linked | rejected | needs_permission | unreachable (+ what is missing, by role only).
`POST /google/reload`  re-read GOOGLE_* from .env (the sign-in script just wrote a new refresh token), then report status.
Neither endpoint ever returns a secret.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_google_auth
from core.env import reload_env

router = APIRouter()


@router.get("/google/status")
async def google_status(auth=Depends(get_google_auth)) -> dict:
    return await auth.status()


@router.post("/google/reload")
async def google_reload(auth=Depends(get_google_auth)) -> dict:
    reload_env("GOOGLE_")
    auth.reset()
    return await auth.status()
