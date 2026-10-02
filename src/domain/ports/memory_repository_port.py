from typing import Protocol, runtime_checkable
from domain.entities.memory_record import MemoryRecord

@runtime_checkable
class MemoryRepositoryPort(Protocol):
    """Reads and writes the cross-agent memory mesh."""

    def record(self, 
        agent_name: str, 
        action: str, context: dict | None = None, 
        user_message: str = "",
    ) -> int:
        """Insert a memory row. Returns its row id."""
        ...

    def recent(self, limit: int = 5) -> list[MemoryRecord]:
        """Last N memory rows across all agents."""
        ...

    def recent_cross_agent(self, exclude: set[str] | None = None, limit: int = 5) -> list[MemoryRecord]:
        """Last N rows excluding the given agent names."""
        ...

    def last_action(self, agent_name: str | None = None) -> MemoryRecord | None:
        """Most recent row, optionally filtered by agent."""
        ...

    def prune(self) -> int:
        """Delete rows past the retention window. Returns count deleted."""
        ...
