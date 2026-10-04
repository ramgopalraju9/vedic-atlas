"""Int16BlockResampler — turns blocks captured at a microphone's own rate into 16 kHz blocks.

Many USB microphones only accept 44.1 / 48 kHz when opened directly, while Veda (VAD, wake word, Whisper)
works on 16 kHz mono. Each call takes one captured block and returns exactly `out_frames` samples. The
previous block is kept as context so the filter does not glitch at block boundaries.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly


class Int16BlockResampler:
    def __init__(self, in_rate: int, out_rate: int, out_frames: int):
        ratio = Fraction(out_rate, in_rate)
        self._up, self._down = ratio.numerator, ratio.denominator
        self._out_frames = out_frames
        self._in_frames = round(out_frames * in_rate / out_rate)   # the input block size that maps onto out_frames
        self._previous = np.zeros(self._in_frames, dtype=np.float64)

    @property
    def in_frames(self) -> int:
        """Block size to open the device with (so every callback maps to one output frame)."""
        return self._in_frames

    def process(self, pcm: bytes) -> bytes:
        block = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        joined = np.concatenate([self._previous, block])
        self._previous = block if block.size == self._in_frames else np.resize(block, self._in_frames)
        out = resample_poly(joined, self._up, self._down)
        out = out[-self._out_frames:] if out.size >= self._out_frames else np.pad(out, (self._out_frames - out.size, 0))
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes()
