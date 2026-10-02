"""KnowledgeBase — standalone facts about the user.

Donor: veda/brain/knowledge.py, read in full. The donor mixed the fact
list (a plain in-memory list of dicts) with its own JSON persistence
(load/save to KNOWLEDGE_FILE) in one class. Split per migration rule 3:
this class holds only the use-case logic against a KnowledgeStorePort;
the JSON file I/O itself is staged at
tpa/filestore/json_knowledge_store.py (a later batch), which implements
that Port.
"""

from typing import Callable

from domain.ports.knowledge_store_port import KnowledgeStorePort


class KnowledgeBase:
    """Use-case facade over a KnowledgeStorePort — add/remove/list/render facts."""

    def __init__(self, store: KnowledgeStorePort, on_change: "Callable[[], None] | None" = None):
        self.store = store
        self._on_change = on_change

    def add_fact(self, fact: str) -> None:
        self.store.add_fact(fact)
        self._notify()

    def remove_fact(self, index: int) -> bool:
        removed = self.store.remove_fact(index)
        if removed:
            self._notify()
        return removed

    def list_facts(self) -> list[str]:
        return self.store.list_facts()

    def get_context(self) -> str:
        return self.store.get_context()

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass