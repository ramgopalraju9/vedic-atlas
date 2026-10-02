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
