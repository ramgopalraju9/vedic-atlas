"""Int16BlockResampler — turns blocks captured at a microphone's own rate into 16 kHz blocks.

Many USB microphones only accept 44.1 / 48 kHz when opened directly, while Veda (VAD, wake word, Whisper)
works on 16 kHz mono. Each call takes one captured block and returns exactly `out_frames` samples. It runs
inside the audio callback, so it must be cheap: the low-pass filter is designed once, and each call filters only
the new block plus a short run of the previous block as context (so the filter does not glitch at block edges).
"""

from __future__ import annotations

from fractions import Fraction
from math import ceil

import numpy as np
from scipy.signal import firwin, resample_poly

_CONTEXT_SAMPLES = 256   # input samples of the previous block kept as filter context (the filter reaches ~55)


class Int16BlockResampler:
    def __init__(self, in_rate: int, out_rate: int, out_frames: int):
        ratio = Fraction(out_rate, in_rate)
        self._up, self._down = ratio.numerator, ratio.denominator
        self._out_frames = out_frames
        self._in_frames = round(out_frames * in_rate / out_rate)   # the input block size that maps onto out_frames
        # Same filter resample_poly would design on every call (scipy's default), designed once here.
        half_len = 10 * max(self._up, self._down)
        self._filter = firwin(2 * half_len + 1, 1.0 / max(self._up, self._down), window=("kaiser", 5.0))
        # The context is a whole number of "down" steps so the output of the context part is a whole number of samples.
        self._context = self._down * ceil(_CONTEXT_SAMPLES / self._down)
        self._context_out = self._context * self._up // self._down
        self._previous = np.zeros(self._context, dtype=np.float64)

    @property
    def in_frames(self) -> int:
        """Block size to open the device with (so every callback maps to one output frame)."""
        return self._in_frames

    def process(self, pcm: bytes) -> bytes:
        block = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        joined = np.concatenate([self._previous, block])
        self._previous = joined[-self._context:]
        out = resample_poly(joined, self._up, self._down, window=self._filter)[self._context_out:]
        out = out[: self._out_frames] if out.size >= self._out_frames else np.pad(out, (0, self._out_frames - out.size))
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes()
