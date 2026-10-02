"""GgufModelManager – resolves and validates local model files.

* New. Centralises the "no auto-download, fail loudly if missing" rule
(REQ-M-02) in one place rather than scattering existence checks across
every adapter that needs a model path. `LlamaCppClient` already checks
its own path defensively; this manager is for server.py's composition
step to validate ALL configured model paths up front, at boot, before
any adapter is constructed – so a missing model fails at startup with a
clear message, not on the first user turn.
"""

from __future__ import annotations

from pathlib import Path


class ModelNotFoundError(FileNotFoundError):
    pass


def require_model(path: str | Path, *, label: str = "model") -> Path:
    """Return the resolved path if it exists; raise a clear error if not."""
    resolved = Path(path)
    if not resolved.exists():
        raise ModelNotFoundError(
            f"{label} not found at {resolved} – this build does not auto-download models. "
            "Run the installer's model-fetch step first."
        )
    return resolved
