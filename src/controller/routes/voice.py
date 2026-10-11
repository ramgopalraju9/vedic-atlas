"""Voice API — status of the always-on listen/think/speak loop.

Donor: veda/routes/voice_config.py, now reading `service/voice/voice_session.py`.

Read-only. Muting is a privacy control and lives on /api/privacy/* behind the
capture gate; the voice session itself starts and stops with the server.
"""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/voice/status")
async def voice_status(request: Request) -> dict:
    s = getattr(request.app.state, "voice_session", None)
    if s is None:
        return {"available": False, "running": False, "reason": "voice session not built"}
    return {"available": True, **s.status()}
