"""ResemblyzerProfileStore — persists speaker embedding vectors to disk.

One file per speaker at `<profiles_dir>/<name>.resemblyzer.npy` — a plain
256-float32 embedding vector (`numpy.save`/`numpy.load`), not an opaque
vendor-exported blob like Eagle's `.eagle` files. Deliberately uses a
distinct extension from `tpa/speaker/profile_store.py`'s `EagleProfileStore`
so both backends can share the same `profiles_dir` without ever colliding
or cross-loading each other's files — see
docs/voice/open-source-wake-speaker-design.md §4.4.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class ResemblyzerProfileStore:
    """Reads/writes one embedding-vector file per enrolled speaker name."""

    _EXT = ".resemblyzer.npy"

    def __init__(self, profiles_dir: str | Path):
        self._dir = Path(profiles_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, speaker_name: str) -> Path:
        return self._dir / f"{speaker_name}{self._EXT}"

    def save(self, speaker_name: str, embedding: np.ndarray) -> None:
        np.save(self.path_for(speaker_name), embedding)

    def load_all(self) -> dict[str, np.ndarray]:
        """All persisted embeddings, keyed by speaker name, sorted by name."""
        return {
            p.name[: -len(self._EXT)]: np.load(p)
            for p in sorted(self._dir.glob(f"*{self._EXT}"))
        }

    def exists(self, speaker_name: str) -> bool:
        return self.path_for(speaker_name).exists()

    def delete(self, speaker_name: str) -> bool:
        path = self.path_for(speaker_name)
        if path.exists():
            path.unlink()
            return True
        return False
