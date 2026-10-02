"""KnowledgeStorePort — persistence for standalone user facts.

Donor: veda/brain/knowledge.py's KnowledgeBase, read in full — method set
below matches its real public API (`add_fact`, `remove_fact`, `list_facts`,
`get_context`) exactly. The donor persisted to a flat JSON file
(KNOWLEDGE_FILE); the file-based adapter is staged at
tpa/filestore/json_knowledge_store.py in a later batch.

Kept distinct from MemoryRepositoryPort: this is simple standalone facts
("I know the user's dog is named Rex"); MemoryRepositoryPort is the
cross-agent action log. The richer, embedding-backed Memory & Recall
feature (Epic 4) is a third, separate concern — its own repository port
lands with that epic's batch.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class KnowledgeStorePort(Protocol):
    """Reads and writes standalone facts about the user."""

    def add_fact(self, fact: str) -> None:
        ...

    def remove_fact(self, index: int) -> bool:
        ...

    def list_facts(self) -> list[str]:
        ...

    def get_context(self) -> str:
        """All facts rendered as a prompt-ready context block."""
        ...