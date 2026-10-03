"""EagleSpeakerRecognizer — implements SpeakerRecognitionPort via `pveagle`.

* New, no donor equivalent. Grounded in the PS-mandatory "must not react
to stray talk" requirement, extended per the user's explicit choice of
multi-speaker recognition: identify WHICH enrolled household member is
talking, not just whether a wake word fired.

Verified against the installed `pveagle` package (not guessed from
vendor docs, which are stale in places - e.g. `Eagle.process()`'s own
docstring references a `.frame_length` property that does not exist on
the recognizer object; the real property is `.min_process_samples`).
Real shape:
  - `pveagle.create_recognizer(access_key)` builds a profile-agnostic
    recognizer ONCE - unlike Porcupine, enrolled profiles are not baked
    in at construction. They're passed on every `process()` call instead.
  - `Eagle.process(pcm: Sequence[int], speaker_profiles) -> Sequence[float] | None`
    returns one score per profile IN THE SAME ORDER as `speaker_profiles`,
    or None if there wasn't enough voice in that frame to score anyone.
  - `pcm` must be a sequence of int16 samples, not raw bytes - this
    adapter does that unpacking, the same `struct.unpack_from` idiom
    tpa/wake_word/porcupine.py already uses, so SpeakerRecognitionPort
    can keep taking plain `bytes` like every other frame-driven port.

`process()` never raises on a missing/corrupt profile at runtime; if zero
profiles are enrolled it short-circuits to `[]` without even calling the
native library, matching PorcupineWakeWord's "degrade, don't fail" shape.
"""

from __future__ import annotations

import struct
from pathlib import Path

from tpa.speaker.profile_store import EagleProfileStore


class EagleSpeakerRecognizer:
    """Implements SpeakerRecognitionPort via pveagle's Eagle recognizer."""

    def __init__(self, access_key: str, profiles_dir: str | Path):
        import pveagle

        self._pveagle = pveagle
        self._access_key = access_key
        self._store = EagleProfileStore(profiles_dir)
        self._eagle = pveagle.create_recognizer(access_key=access_key)
        self._names: list[str] = []
        self._profiles: list = []
        self._load_from_store()

    def _load_from_store(self) -> None:
        loaded = self._store.load_all()
        self._names = list(loaded.keys())
        self._profiles = [self._pveagle.EagleProfile.from_bytes(b) for b in loaded.values()]

    @property
    def frame_length(self) -> int:
        # Eagle's recognizer exposes this as `min_process_samples`, not
        # `frame_length` (that name only exists on EagleProfiler) - the
        # port hides that vendor inconsistency behind one clean name.
        return self._eagle.min_process_samples

    @property
    def sample_rate(self) -> int:
        return self._eagle.sample_rate

    @property
    def speaker_names(self) -> list[str]:
        return list(self._names)

    def process(self, frame: bytes) -> list[float]:
        if not self._profiles:
            return []
        pcm = struct.unpack_from("h" * self.frame_length, frame)
        try:
            scores = self._eagle.process(pcm, self._profiles)
        except Exception:
            return [0.0] * len(self._names)
        if scores is None:
            # Not enough voice in this frame to score anyone - zeros, not
            # an error; the caller averages over many frames per utterance.
            return [0.0] * len(self._names)
        return list(scores)

    def reload_profiles(self) -> None:
        self._load_from_store()

    def stop(self) -> None:
        delete = getattr(self._eagle, "delete", None)
        if callable(delete):
            try:
                delete()
            except Exception:
                pass
