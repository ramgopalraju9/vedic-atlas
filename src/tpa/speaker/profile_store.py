"""EagleProfileStore — persists exported speaker-profile bytes to disk.

One file per speaker at `<profiles_dir>/<name>.eagle`, raw exported bytes
(`EagleProfile.to_bytes()`'s output). Deliberately vendor-agnostic itself
(no pveagle import) — it just stores bytes under a name; the recognizer
and enroller adapters own turning those bytes into/from an EagleProfile.
A directory of blobs, not a DB table, matching the existing `.ppn`
wake-word keyword-file precedent (a loose file on disk, loaded at boot).
"""

from __future__ import annotations

from pathlib import Path


class EagleProfileStore:
    """Reads/writes one profile-bytes file per enrolled speaker name."""

    def __init__(self, profiles_dir: str | Path):
        self._dir = Path(profiles_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, speaker_name: str) -> Path:
        return self._dir / f"{speaker_name}.eagle"

    def save(self, speaker_name: str, profile_bytes: bytes) -> None:
        self.path_for(speaker_name).write_bytes(profile_bytes)

    def load_all(self) -> dict[str, bytes]:
        """All persisted profiles, keyed by speaker name, sorted by name."""
        return {p.stem: p.read_bytes() for p in sorted(self._dir.glob("*.eagle"))}

    def exists(self, speaker_name: str) -> bool:
        return self.path_for(speaker_name).exists()

    def delete(self, speaker_name: str) -> bool:
        path = self.path_for(speaker_name)
        if path.exists():
            path.unlink()
            return True
        return False
