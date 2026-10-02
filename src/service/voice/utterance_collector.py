"""UtteranceCollector — turns a stream of VAD-classified frames into utterances.

★ New. Deliberately a pure state machine with no I/O, no asyncio and no
knowledge of microphones: you feed it frames, it hands back an Utterance
when one completes. That makes the trickiest part of the voice pipeline
(deciding when someone has finished speaking) unit-testable without any
audio hardware.

    silence ──speech frame──> triggering ──enough speech──> SPEAKING
    SPEAKING ──enough silence──> emit Utterance ──> silence

Two guards, both real-world necessities:
  * `start_frames` — requires N consecutive speech frames before opening
    an utterance, so a single keyboard click or cough doesn't start one.
  * `max_duration_sec` — force-closes a runaway utterance (e.g. a TV
    playing in the room) so the pipeline can't be starved indefinitely.
"""

from __future__ import annotations

from datetime import datetime

from domain.entities.utterance import Utterance
from domain.ports.vad_port import VoiceActivityPort
from domain.value_objects.audio_window import AudioWindow


class UtteranceCollector:
    """Accumulates speech frames between silences into complete utterances."""

    def __init__(
        self,
        vad: VoiceActivityPort,
        *,
        start_frames: int = 3,
        silence_ms: int = 700,
        max_duration_sec: float = 15.0,
        pre_roll_frames: int = 5,
    ):
        self._vad = vad
        self._start_frames = start_frames
        self._silence_frames_needed = max(1, silence_ms // vad.frame_duration_ms)
        self._max_duration_sec = max_duration_sec
        self._pre_roll_frames = pre_roll_frames

        self._speaking = False
        self._consecutive_speech = 0
        self._consecutive_silence = 0
        self._frames: list[bytes] = []
        # Keeps the moments just before speech was confirmed, so the
        # utterance doesn't start with a clipped first syllable.
        self._pre_roll: list[bytes] = []
        self._started_at: datetime | None = None
        self._sample_rate = 16_000
        self._channels = 1

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def reset(self) -> None:
        """Drop any in-progress utterance. Used when capture is muted mid-speech."""
        self._speaking = False
        self._consecutive_speech = 0
        self._consecutive_silence = 0
        self._frames.clear()
        self._pre_roll.clear()
        self._started_at = None

    def push(self, frame: AudioWindow) -> Utterance | None:
        """Feed one frame. Returns a completed Utterance, or None."""
        self._sample_rate = frame.sample_rate
        self._channels = frame.channels
        speech = self._vad.is_speech(frame)

        if not self._speaking:
            if speech:
                self._consecutive_speech += 1
                self._pre_roll.append(frame.pcm)
                if self._consecutive_speech >= self._start_frames:
                    self._speaking = True
                    self._started_at = datetime.now()
                    self._frames = list(self._pre_roll)
                    self._pre_roll.clear()
                    self._consecutive_silence = 0
            else:
                self._consecutive_speech = 0
                self._pre_roll.append(frame.pcm)
                if len(self._pre_roll) > self._pre_roll_frames:
                    self._pre_roll.pop(0)
            return None

        self._frames.append(frame.pcm)
        if speech:
            self._consecutive_silence = 0
        else:
            self._consecutive_silence += 1
            if self._consecutive_silence >= self._silence_frames_needed:
                return self._close(truncated=False)

        if self._duration_sec() >= self._max_duration_sec:
            return self._close(truncated=True)
        return None

    def _duration_sec(self) -> float:
        frame_sec = self._vad.frame_duration_ms / 1000.0
        return len(self._frames) * frame_sec

    def _close(self, *, truncated: bool) -> Utterance:
        pcm = b"".join(self._frames)
        duration = self._duration_sec()
        started = self._started_at or datetime.now()
        frame_count = len(self._frames)
        self.reset()
        return Utterance(
            audio=AudioWindow(
                pcm=pcm,
                sample_rate=self._sample_rate,
                channels=self._channels,
                started_at=started,
                duration_sec=duration,
            ),
            started_at=started,
            duration_sec=duration,
            frame_count=frame_count,
            truncated=truncated,
        )