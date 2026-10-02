


"""Voice API — status and control for the always-on listen/think/speak loop.

Donor: veda/routes/voice_config.py (the browser wake-word config endpoint),
now extended to drive `service/voice/voice_session.py`.

Deliberately does NOT expose mute here: muting is a privacy control and
lives on /api/privacy/* behind the capture gate. Stopping the voice
session is an availability control — a stopped session is not the same
claim as a muted microphone, and conflating them would let the UI imply
the mic is off when it merely isn't being read.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class VoiceBrowserConfig(BaseModel):
    wake_word_enabled: bool


def _session(request: Request):
    s = getattr(request.app.state, "voice_session", None)
    if s is None:
        raise HTTPException(
            status_code=503,
            detail="voice session unavailable — check server log for the missing component "
                   "(mic / vad / stt / tts / speaker)",
        )
    return s


@router.get("/voice/config", response_model=VoiceBrowserConfig)
async def voice_config(request: Request):
    full = request.app.state.full_config
    return VoiceBrowserConfig(wake_word_enabled=full.audio.wake_word_enabled)


@router.get("/voice/status")
async def voice_status(request: Request) -> dict:
    s = getattr(request.app.state, "voice_session", None)
    if s is None:
        return {"available": False, "running": False, "reason": "voice session not built"}
    return {"available": True, **s.status()}


@router.post("/voice/start")
async def voice_start(request: Request) -> dict:
    s = _session(request)
    s.start()
    return {"available": True, **s.status()}


@router.post("/voice/stop")
async def voice_stop(request: Request) -> dict:
    s = _session(request)
    await s.stop()
    return {"available": True, **s.status()}


class SayBody(BaseModel):
    text: str


@router.post("/voice/say")
async def voice_say(body: SayBody, request: Request) -> dict:
    """Speak arbitrary text — lets a demo prove TTS without waiting for a turn."""
    s = _session(request)
    await s.speak(body.text)
    return {"spoken": body.text}