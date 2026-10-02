"""PorcupineWakeWord — implements WakeWordPort via `pvporcupine`.

Donor: veda/voice/wakeword.py's WakeWordDetector, read in full (Batch 3
grounding). `frame_length`/`sample_rate` properties ported verbatim.
`process()` is new — the donor's class exposed the raw porcupine object
but no per-frame method was found in the file itself (it's presumably
called directly on `_porcupine` from the daemon, which wasn't read in
this migration); this method wraps porcupine's real `process(pcm) -> int`
API (returns a keyword index, -1 if none) into the WakeWordPort's `bool`
contract.
"""

from __future__ import annotations

from pathlib import Path


class PorcupineWakeWord:
    """Implements WakeWordPort via pvporcupine."""

    def __init__(self, access_key: str, keyword_path: str | Path):
        path = Path(keyword_path)
        if not path.exists():
            raise FileNotFoundError(f"wake-word keyword file not found at {path}")
        import pvporcupine

        self._porcupine = pvporcupine.create(access_key=access_key, keyword_paths=[str(path)])

    @property
    def frame_length(self) -> int:
        return self._porcupine.frame_length

    @property
    def sample_rate(self) -> int:
        return self._porcupine.sample_rate

    def process(self, frame: bytes) -> bool:
        import struct

        pcm = struct.unpack_from("h" * self._porcupine.frame_length, frame)
        return self._porcupine.process(pcm) >= 0

    def start(self) -> None:
        # Frame-driven; no background thread. Present so the voice loop can drive
        # every wake engine through the same start/stop pair.
        pass

    def stop(self) -> None:
        delete = getattr(self._porcupine, "delete", None)
        if callable(delete):
            try:
                delete()
            except Exception:
                pass