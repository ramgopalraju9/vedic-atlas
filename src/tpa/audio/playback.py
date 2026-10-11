"""SpeakerPlayback — plays PCM chunks through the default output device.

Donor: veda/voice/audio_io.py's SpeakerPlayer, read in full (Batch 3
grounding). A worker thread serializes playback so sentence-by-sentence
TTS output queues cleanly instead of overlapping.

`wait_done()` was added after a real feedback-loop bug: VoiceSession was
*estimating* playback duration from PCM length and re-opening the mic when
that estimate expired. Playback actually starts late (queue hand-off plus
`sd.play` startup latency), so the mic re-opened while the speaker was
still talking — Veda transcribed its own voice and answered itself in a
loop. Callers now wait for real completion instead of guessing.
"""

from __future__ import annotations

import queue
import threading
import time

from core.logging_config import (
    get_voice_turn_id,
    logger,
    reset_voice_turn_id,
    set_voice_turn_id,
)


class SpeakerPlayback:
    """Serialized PCM playback via sounddevice, one worker thread."""

    def __init__(self, device_index: int | None = None):
        self.device_index = device_index
        self._queue: "queue.Queue[tuple[bytes, int, float, str] | None]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # Set while nothing is queued AND nothing is mid-playback.
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put_nowait(None)
        if self._worker is not None:
            self._worker.join(timeout=2)
            self._worker = None

    def _run(self) -> None:
        import numpy as np
        import sounddevice as sd

        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                return
            pcm_bytes, sample_rate, queued_at, turn_id = item
            token = set_voice_turn_id(turn_id)
            try:
                queue_ms = (time.perf_counter() - queued_at) * 1000
                audio_sec = len(pcm_bytes) / (2 * max(sample_rate, 1))
                playback_started = time.perf_counter()
                logger.info(
                    "[speaker][timing] stage=playback_start "
                    f"queue_ms={queue_ms:.0f} audio_sec={audio_sec:.2f} "
                    f"device={self.device_index if self.device_index is not None else 'default'}"
                )
                pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
                sd.play(pcm, samplerate=sample_rate, device=self.device_index, blocking=True)
            except Exception as e:
                logger.error(f"[speaker] playback error: {e}")
            finally:
                logger.info(
                    "[speaker][timing] stage=playback_complete "
                    f"elapsed_ms={(time.perf_counter() - playback_started) * 1000:.0f} "
                    f"audio_sec={audio_sec:.2f}"
                )
                with self._lock:
                    self._pending -= 1
                    if self._pending <= 0:
                        self._pending = 0
                        self._idle.set()
                reset_voice_turn_id(token)

    def play_pcm(self, pcm_bytes: bytes, sample_rate: int) -> None:
        """Queue a PCM chunk for playback. Non-blocking."""
        with self._lock:
            self._pending += 1
            self._idle.clear()
        turn_id = get_voice_turn_id()
        self._queue.put_nowait((pcm_bytes, sample_rate, time.perf_counter(), turn_id))

    def interrupt(self) -> None:
        """Stop what is playing NOW and drop everything queued (used when the user mutes mid-reply)."""
        dropped = 0
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:  # keep a pending stop() sentinel
                    self._queue.put_nowait(None)
                    break
                dropped += 1
        except queue.Empty:
            pass
        with self._lock:
            self._pending = max(0, self._pending - dropped)
            if self._pending == 0:
                self._idle.set()
        try:
            import sounddevice as sd

            sd.stop()  # makes the blocking sd.play() in the worker return; its `finally` settles the count
        except Exception as e:
            logger.warning(f"[speaker] interrupt: {e}")

    @property
    def is_playing(self) -> bool:
        return not self._idle.is_set()

    def wait_done(self, timeout: float = 60.0) -> bool:
        """Block until everything queued has finished playing.

        Returns False on timeout so a stuck audio device can't wedge the
        voice loop permanently.
        """
        return self._idle.wait(timeout=timeout)