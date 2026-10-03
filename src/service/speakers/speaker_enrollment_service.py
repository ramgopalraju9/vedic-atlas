"""SpeakerEnrollmentService — records a household member's voice profile.

Thin facade over SpeakerEnrollmentPort + AudioCapturePort, same shape as
service/tasks/task_service.py over TaskRepositoryPort. No tpa/ import, no
VoiceSession dependency - this only knows about ports.

Mic-sharing precondition (documented, not silently engineered around):
this reads from the same AudioCapturePort instance the always-on voice
loop polls. The caller (controller/routes/speakers.py) is responsible for
ensuring nothing else is reading the mic for the duration of a session -
concretely, the voice session must be stopped and the capture gate must
be unmuted before `start()` is called. This service does not reach into
CaptureGate/VoiceSession itself (it only depends on the two ports above),
so that precondition lives at the route, where app.state is available.
"""

from __future__ import annotations

import asyncio
import time

from core.enums import ErrorMessage, ExceptionCode
from domain.ports.audio_capture_port import AudioCapturePort
from domain.ports.speaker_enrollment_port import SpeakerEnrollmentPort
from exceptions.exception import AppException


class SpeakerEnrollmentService:
    """Drives one enroll-a-speaker-from-the-live-mic session at a time."""

    def __init__(self, enroller: SpeakerEnrollmentPort, audio: AudioCapturePort):
        self._enroller = enroller
        self._audio = audio
        self._active_name: str | None = None
        self._buffer = b""

    def start(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise AppException(
                "SpeakerEnrollmentService", ExceptionCode.VALIDATION_ERROR,
                message="Speaker name is required",
            )
        if name in self._enroller.list_enrolled():
            raise AppException(
                "SpeakerEnrollmentService", ExceptionCode.VALIDATION_ERROR,
                message=f"'{name}' is already enrolled - delete it first to re-enroll",
            )
        self._enroller.enroll_reset()
        self._buffer = b""
        self._active_name = name

    async def feed(self, *, seconds: float = 1.0) -> tuple[float, str]:
        if self._active_name is None:
            raise AppException(
                "SpeakerEnrollmentService", ExceptionCode.VALIDATION_ERROR,
                message="No enrollment session in progress - call start first",
            )
        frame_bytes = self._enroller.frame_length * 2  # int16 PCM = 2 bytes/sample
        percentage, feedback = 0.0, "collecting"
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            window = await asyncio.to_thread(self._audio.read, 0.5)
            if window is None:
                continue
            self._buffer += window.pcm
            while len(self._buffer) >= frame_bytes:
                chunk, self._buffer = self._buffer[:frame_bytes], self._buffer[frame_bytes:]
                percentage, feedback = self._enroller.enroll_feed(chunk)
                if percentage >= 100.0:
                    return percentage, feedback
        return percentage, feedback

    def finish(self) -> str:
        if self._active_name is None:
            raise AppException(
                "SpeakerEnrollmentService", ExceptionCode.VALIDATION_ERROR,
                message="No enrollment session in progress - call start first",
            )
        name = self._active_name
        try:
            self._enroller.enroll_finish(name)
        except AppException:
            raise
        except Exception as e:
            raise AppException(
                "SpeakerEnrollmentService", ExceptionCode.VALIDATION_ERROR,
                error_message=ErrorMessage.GENERIC,
                detail=f"enrollment incomplete or failed: {e}",
            ) from e
        self._active_name = None
        self._buffer = b""
        return name

    def cancel(self) -> None:
        self._enroller.enroll_reset()
        self._active_name = None
        self._buffer = b""

    def list_enrolled(self) -> list[str]:
        return self._enroller.list_enrolled()

    def delete(self, name: str) -> bool:
        deleted = self._enroller.delete_enrolled(name)
        if not deleted:
            raise AppException(
                "SpeakerEnrollmentService", ExceptionCode.NOT_FOUND,
                message=f"No enrolled speaker named '{name}'",
            )
        return deleted
