"""SoundDeviceCapture — implements AudioCapturePort via `sounddevice`.

Donor: veda/voice/audio_io.py's MicStream, read in full (Batch 3
grounding). Ported closely: `start()`/`stop()`/`read()` map directly to
MicStream's real methods. `read()` now wraps the returned int16 numpy
array in a domain AudioWindow rather than returning a bare array — so
nothing above this adapter ever sees a vendor-specific buffer type,
per REQ-M-06 (audio stays behind one well-defined boundary).
"""

from __future__ import annotations

import queue
from datetime import datetime

from domain.value_objects.audio_window import AudioWindow

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"


class SoundDeviceCapture:
    """Implements AudioCapturePort using sounddevice.RawInputStream."""

    def __init__(self, frame_length: int, device_index: int | None = None):
        self.frame_length = frame_length
        self.device_index = device_index
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._stream = None

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def channels(self) -> int:
        return CHANNELS

    def _callback(self, indata, frames, time_info, status):
        if status:
            from core.logging_config import logger

            logger.warning(f"[mic] {status}")
        data = bytes(indata)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(data)

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
            blocksize=self.frame_length, device=self.device_index, callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def read(self, timeout: float = 1.0) -> AudioWindow | None:
        try:
            pcm = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        duration_sec = self.frame_length / SAMPLE_RATE
        return AudioWindow(pcm=pcm, sample_rate=SAMPLE_RATE, channels=CHANNELS, started_at=datetime.now(), duration_sec=duration_sec)

    def drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return