"""Lightweight offline spectral noise suppression for in-memory speech audio."""

from __future__ import annotations

import numpy as np


class SpectralNoiseSuppressor:
    """Reduce steady background noise using a per-utterance spectral gate.

    The noise spectrum is estimated from the lowest-energy analysis frames in
    the utterance. This is intended for steady fans, hum, and room noise; it
    is not a replacement for a neural denoiser in rapidly changing noise.
    """

    def __init__(self, strength: float = 0.75):
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"strength must be between 0 and 1, got {strength}")
        self._strength = strength

    @property
    def strength(self) -> float:
        return self._strength

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Return a denoised float32 mono signal without modifying the input."""
        signal = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self._strength == 0.0 or signal.size < 512:
            return signal.copy()

        fft_size = 512
        hop_size = 160
        window = np.hanning(fft_size + 1)[:-1].astype(np.float32)
        half_window = fft_size // 2
        padded = np.pad(signal, (half_window, half_window))
        frame_count = max(1, int(np.ceil((padded.size - fft_size) / hop_size)) + 1)
        required_size = fft_size + (frame_count - 1) * hop_size
        if padded.size < required_size:
            padded = np.pad(padded, (0, required_size - padded.size))

        frames = np.lib.stride_tricks.sliding_window_view(padded, fft_size)[::hop_size]
        frames = frames[:frame_count]
        spectrum = np.fft.rfft(frames * window, axis=1)
        magnitude = np.abs(spectrum)

        frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
        noise_frame_count = max(1, int(np.ceil(frame_count * 0.2)))
        quiet_frames = np.argpartition(frame_rms, noise_frame_count - 1)[:noise_frame_count]
        noise_magnitude = np.median(magnitude[quiet_frames], axis=0)
        noise_power = np.square(noise_magnitude * self._strength)
        signal_power = np.square(magnitude)
        gain = np.sqrt(np.maximum(1.0 - noise_power[None, :] / (signal_power + 1e-10), 0.04))

        filtered_frames = np.fft.irfft(spectrum * gain, n=fft_size, axis=1).real
        output = np.zeros(required_size, dtype=np.float64)
        normalization = np.zeros(required_size, dtype=np.float64)
        window64 = window.astype(np.float64)
        for index, frame in enumerate(filtered_frames):
            start = index * hop_size
            output[start:start + fft_size] += frame * window64
            normalization[start:start + fft_size] += window64 * window64

        valid = normalization > 1e-8
        output[valid] /= normalization[valid]
        output = output[half_window:half_window + signal.size]
        return np.clip(output, -1.0, 1.0).astype(np.float32)
