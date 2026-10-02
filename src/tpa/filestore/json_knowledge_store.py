"""JsonKnowledgeStore – flat-file JSON persistence for KnowledgeStorePort.

Donor: veda/brain/knowledge.py's `_load`/`_save` half, read in full
(Batch 3 grounding) and split out per migration rule 3 – the use-case
logic (add/remove/list/context) stays in service/memory/knowledge_base.py,
this class is only the file I/O.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.constants import DATA_DIR

_DEFAULT_PATH = DATA_DIR / "knowledge.json"


class JsonKnowledgeStore:
    """Persists facts as a flat JSON list of {"fact", "added"} objects."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else _DEFAULT_PATH
        self._facts: list[dict] = []
        self._load()

    def add_fact(self, fact: str) -> None:
        self._facts.append({"fact": fact, "added": datetime.now().isoformat(timespec="seconds")})
        self._save()

    def remove_fact(self, index: int) -> bool:
        if 0 <= index < len(self._facts):
            self._facts.pop(index)
            self._save()
            return True
        return False

    def list_facts(self) -> list[str]:
        return [f["fact"] for f in self._facts]

    def get_context(self) -> str:
        if not self._facts:
            return ""
        lines = [f"- {f['fact']}" for f in self._facts]
        return "THINGS I KNOW ABOUT THE USER:\n" + "\n".join(lines)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._facts, f, indent=2)

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r") as f:
                    self._facts = json.load(f)
            except (json.JSONDecodeError, KeyError):
                self._facts = []
