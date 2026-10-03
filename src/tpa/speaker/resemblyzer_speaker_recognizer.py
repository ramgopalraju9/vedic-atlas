"""ResemblyzerSpeakerRecognizer — implements SpeakerRecognitionPort via `resemblyzer`.

★ New. Open-source, no-API-key replacement for EagleSpeakerRecognizer —
see docs/voice/open-source-wake-speaker-design.md §4.2 for the full
design and §3.3 for why this adapter is windowed rather than frame-driven.

Resemblyzer has no per-frame scoring API at all: `VoiceEncoder.embed_utterance()`
takes a chunk of audio (recommended at least ~1.5-2s for a stable
embedding) and returns one 256-dim, L2-normalized embedding for that
whole chunk — there is no way to get a meaningful score from a single
30ms frame the way Eagle's `process()` does. This adapter buffers raw
PCM into a rolling window and only recomputes scores once
`score_window_sec` worth of audio has accumulated; between recomputes it
returns the last cached scores. `VoiceSession` already expects exactly
this shape (it accumulates per-call scores into a running mean over the
whole armed window and decides once in `_resolve_speaker()`, never
per-frame), so a port implementation that updates its answer every N
calls instead of every call needs no caller-side change.

`frame_length` is NOT 0 despite Resemblyzer being frame-size-agnostic:
`VoiceSession._feed_speaker_frame()` treats `frame_length <= 0` as "this
port is disabled, don't call process() at all" (the convention
HotkeyWakeWord relies on for its push-to-talk shape). Reporting a real
chunk size here is what keeps the feed loop active; the exact value
doesn't matter to Resemblyzer itself, only that it's nonzero.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tpa.speaker.resemblyzer_profile_store import ResemblyzerProfileStore

_FRAME_SAMPLES = 1280  # 80ms @ 16kHz — arbitrary but nonzero, see module docstring


class ResemblyzerSpeakerRecognizer:
    """Implements SpeakerRecognitionPort via resemblyzer's VoiceEncoder."""

    def __init__(
        self,
        profiles_dir: str | Path,
        score_window_sec: float = 2.0,
        sample_rate: int = 16000,
    ):
        from resemblyzer import VoiceEncoder

        self._encoder = VoiceEncoder("cpu")
        self._store = ResemblyzerProfileStore(profiles_dir)
        self._profiles: dict[str, np.ndarray] = self._store.load_all()
        self._sample_rate = sample_rate
        self._window_samples = int(score_window_sec * sample_rate)
        self._buf = bytearray()
        self._last_scores: list[float] = [0.0] * len(self._profiles)

    @property
    def frame_length(self) -> int:
        return _FRAME_SAMPLES

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def speaker_names(self) -> list[str]:
        return list(self._profiles.keys())

    def process(self, frame: bytes) -> list[float]:
        if not self._profiles:
            return []
        self._buf += frame
        window_bytes = self._window_samples * 2  # int16 PCM = 2 bytes/sample
        if len(self._buf) < window_bytes:
            return self._last_scores
        # Keep only the most recent window's worth of audio - this is a
        # sliding recompute, not an accumulate-forever buffer.
        window, self._buf = bytes(self._buf[-window_bytes:]), bytearray()
        pcm = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            embedding = self._encoder.embed_utterance(pcm)
        except Exception:
            return self._last_scores
        # Both embedding and each stored profile are L2-normalized, so the
        # dot product is exactly the cosine similarity.
        self._last_scores = [float(np.dot(embedding, profile)) for profile in self._profiles.values()]
        return self._last_scores

    def reload_profiles(self) -> None:
        self._profiles = self._store.load_all()
        self._last_scores = [0.0] * len(self._profiles)
