"""FilePromptStore — versioned prompt text from config/prompts/<name>.md.

Prompts live in plain files (not Python strings) so they can be reviewed,
diffed, token-counted and tuned without touching code. Read once and cached.
"""

from __future__ import annotations

from pathlib import Path

from core.constants import CONFIG_DIR
from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException

_DEFAULT_DIR = CONFIG_DIR / "prompts"


class FilePromptStore:
    """Implements PromptStorePort."""

    def __init__(self, directory: str | Path | None = None):
        self._dir = Path(directory) if directory else _DEFAULT_DIR
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = self._dir / f"{name}.md"
            if not path.is_file():
                raise AppException(
                    class_name="FilePromptStore",
                    code=ExceptionCode.CONFIG_ERROR,
                    error_message=ErrorMessage.PROMPT_NOT_FOUND,
                    name=name,
                    path=str(self._dir),
                )
            self._cache[name] = path.read_text(encoding="utf-8").strip()
        return self._cache[name]

    def names(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.md"))
