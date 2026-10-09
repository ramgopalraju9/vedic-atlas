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
import threading
from datetime import datetime

from domain.value_objects.audio_window import AudioWindow

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"
_FALLBACK_RATES = (48_000, 44_100)   # tried after the device's own default rate when 16 kHz is refused


class SoundDeviceCapture:
    """Implements AudioCapturePort using sounddevice.RawInputStream."""

    def __init__(self, frame_length: int, device_index: int | None = None):
        self.frame_length = frame_length
        self.device_index = device_index
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._stream = None
        self._resampler = None   # set only when the mic cannot be opened at 16 kHz
        self._queue_dropped_frames = 0
        self._stats_lock = threading.Lock()
        self._input_overflow_count = 0

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def channels(self) -> int:
        return CHANNELS

    def _callback(self, indata, frames, time_info, status):
        if status:
            from core.logging_config import logger

            if getattr(status, "input_overflow", False):
                self._input_overflow_count += 1
            logger.warning(
                f"[mic] input callback status={status} "
                f"input_overflow_count={self._input_overflow_count}"
            )
        data = bytes(indata)
        if self._resampler is not None:
            data = self._resampler.process(data)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            try:
                self._queue.get_nowait()
                with self._stats_lock:
                    self._queue_dropped_frames += 1
            except queue.Empty:
                pass
            self._queue.put_nowait(data)

    def start(self) -> None:
        import sounddevice as sd
        from core.logging_config import logger

        self._resampler = None
        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                blocksize=self.frame_length, device=self.device_index, callback=self._callback,
                latency="high",
            )
        except Exception as refused:
            # Many USB microphones only accept 44.1 / 48 kHz when opened directly ("Invalid sample rate").
            self._stream = self._open_at_native_rate(sd, refused)
        self._stream.start()
        try:
            device = sd.query_devices(self.device_index, "input")
            device_name = device["name"]
        except Exception:
            device_name = self.device_index if self.device_index is not None else "system-default"
        logger.info(
            "[mic] capture stream opened "
            f"device={device_name!r} sample_rate={SAMPLE_RATE}Hz "
            f"frame_samples={self.frame_length}"
        )

    def _open_at_native_rate(self, sd, refused: Exception):
        """Open the mic at a rate it accepts and convert every block to 16 kHz; re-raise `refused` if none works."""
        from core.logging_config import logger
        from tpa.audio.resampler import Int16BlockResampler

        try:
            default_rate = int(sd.query_devices(self.device_index, "input")["default_samplerate"])
        except Exception:
            default_rate = 0
        for rate in dict.fromkeys(r for r in (default_rate, *_FALLBACK_RATES) if r > 0):
            resampler = Int16BlockResampler(rate, SAMPLE_RATE, self.frame_length)
            try:
                stream = sd.RawInputStream(
                    samplerate=rate, channels=CHANNELS, dtype=DTYPE,
                    blocksize=resampler.in_frames, device=self.device_index, callback=self._callback,
                    latency="high",   # bigger device buffer: tolerates a late callback ("input overflow") on a slow Pi
                )
            except Exception:
                continue
            self._resampler = resampler
            logger.warning(
                f"[mic] cannot open at {SAMPLE_RATE} Hz ({str(refused).splitlines()[0]}); "
                f"opened at {rate} Hz and converting to {SAMPLE_RATE} Hz"
            )
            return stream
        raise refused

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
                from core.logging_config import logger

                logger.info("[mic] capture stream closed")
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

    def drain(self) -> int:
        """Discard queued frames and return how many were dropped."""
        with self._stats_lock:
            drained = self._queue_dropped_frames
            self._queue_dropped_frames = 0
        while True:
            try:
                self._queue.get_nowait()
                drained += 1
            except queue.Empty:
                return drained