"""Speaker enrollment API — teaches Eagle a household member's voice.

Kept as its own route group, not bolted onto /voice, for the same reason
mute control lives under /privacy rather than /voice: a different concern
with a different lifecycle (one-time setup vs. the always-on session).

Enrollment reads from the same AudioCapturePort the voice loop polls, so
it requires the voice session stopped AND the mic unmuted first (see
service/speakers/speaker_enrollment_service.py's docstring for why this
precondition exists rather than being silently engineered around).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from controller.dependencies.providers import get_speaker_enrollment_service
from core.enums import ExceptionCode
from exceptions.exception import AppException
from schemas.speakers import SpeakerEnrollProgress, SpeakerEnrollResult, SpeakerEnrollStart, SpeakerList
from service.speakers.speaker_enrollment_service import SpeakerEnrollmentService

router = APIRouter()


def _check_mic_available(request: Request) -> None:
    voice_session = getattr(request.app.state, "voice_session", None)
    if voice_session is not None and voice_session.is_running:
        raise AppException(
            "speakers", ExceptionCode.VALIDATION_ERROR,
            message="Stop the voice session (POST /api/voice/stop) before enrolling a speaker",
        )
    gate = getattr(request.app.state, "capture_gate", None)
    if gate is not None and gate.is_muted():
        raise AppException(
            "speakers", ExceptionCode.VALIDATION_ERROR,
            message="Unmute the mic (POST /api/privacy/mute) before enrolling a speaker",
        )


def _reload_recognizer(request: Request) -> None:
    recognizer = getattr(request.app.state, "speaker_recognizer", None)
    if recognizer is not None:
        recognizer.reload_profiles()


@router.get("/speakers", response_model=SpeakerList)
async def list_speakers(service: SpeakerEnrollmentService = Depends(get_speaker_enrollment_service)):
    return SpeakerList(speakers=service.list_enrolled())


@router.post("/speakers/enroll/start", response_model=SpeakerEnrollResult)
async def start_enrollment(
    body: SpeakerEnrollStart,
    request: Request,
    service: SpeakerEnrollmentService = Depends(get_speaker_enrollment_service),
):
    _check_mic_available(request)
    service.start(body.name)
    return SpeakerEnrollResult(enrolled=body.name)


@router.post("/speakers/enroll/feed", response_model=SpeakerEnrollProgress)
async def feed_enrollment(service: SpeakerEnrollmentService = Depends(get_speaker_enrollment_service)):
    percentage, feedback = await service.feed()
    return SpeakerEnrollProgress(percentage=percentage, feedback=feedback)


@router.post("/speakers/enroll/finish", response_model=SpeakerEnrollResult)
async def finish_enrollment(
    request: Request,
    service: SpeakerEnrollmentService = Depends(get_speaker_enrollment_service),
):
    name = service.finish()
    _reload_recognizer(request)
    return SpeakerEnrollResult(enrolled=name)


@router.post("/speakers/enroll/cancel")
async def cancel_enrollment(service: SpeakerEnrollmentService = Depends(get_speaker_enrollment_service)) -> dict:
    service.cancel()
    return {"status": "cancelled"}


@router.delete("/speakers/{name}")
async def delete_speaker(
    name: str,
    request: Request,
    service: SpeakerEnrollmentService = Depends(get_speaker_enrollment_service),
) -> dict:
    service.delete(name)
    _reload_recognizer(request)
    return {"status": "deleted", "name": name}
