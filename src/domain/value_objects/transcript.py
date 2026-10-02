"""Transcript — the result of a speech-to-text pass.

New — the donor's voice/stt.py returned a bare string. Wrapping it as a
value object lets the pipeline carry confidence and partial/final state
without every caller re-deriving them from ad-hoc tuples.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    """Immutable STT result for one utterance or partial segment."""

    text: str
    confidence: float
    language: str = "en"
    is_final: bool = True
    duration_sec: float = 0.0