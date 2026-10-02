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

from core.logging_config import logger


class SpeakerPlayback:
    """Serialized PCM playback via sounddevice, one worker thread."""

    def __init__(self, device_index: int | None = None):
        self.device_index = device_index
        self._queue: "queue.Queue[tuple[bytes, int] | None]" = queue.Queue()
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
            pcm_bytes, sample_rate = item
            try:
                pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
                sd.play(pcm, samplerate=sample_rate, device=self.device_index, blocking=True)
            except Exception as e:
                logger.error(f"[speaker] playback error: {e}")
            finally:
                with self._lock:
                    self._pending -= 1
                    if self._pending <= 0:
                        self._pending = 0
                        self._idle.set()

    def play_pcm(self, pcm_bytes: bytes, sample_rate: int) -> None:
        """Queue a PCM chunk for playback. Non-blocking."""
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._queue.put_nowait((pcm_bytes, sample_rate))

    @property
    def is_playing(self) -> bool:
        return not self._idle.is_set()

    def wait_done(self, timeout: float = 60.0) -> bool:
        """Block until everything queued has finished playing.

        Returns False on timeout so a stuck audio device can't wedge the
        voice loop permanently.
        """
        return self._idle.wait(timeout=timeout)