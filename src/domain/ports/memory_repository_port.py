"""MemoryRepositoryPort — persistence for the cross-agent memory mesh.

Donor: veda/db/agent_memory_repo.py's AgentMemoryRepository, read in full.
Method set below is a direct, grounded match to the donor's real public
API: `record`, `recent`, `recent_cross_agent`, `last_action`, `prune`.
`last_specialist` and `last_project` are donor-specific supervisor-routing
helpers built on top of these primitives — they belong in
service/agent/supervisor.py (built against this port), not in the port
itself.
"""

from typing import Protocol, runtime_checkable

from domain.entities.memory_record import MemoryRecord


@runtime_checkable
class MemoryRepositoryPort(Protocol):
    """Reads and writes the cross-agent memory mesh."""

    def record(
        self,
        agent_name: str,
        action: str,
        context: dict | None = None,
        user_message: str = "",
    ) -> int:
        """Insert a memory row. Returns its row id."""
        ...

    def recent(self, limit: int = 5) -> list[MemoryRecord]:
        """Last N memory rows across all agents, chronological."""
        ...

    def recent_cross_agent(
        self, exclude: set[str] | None = None, limit: int = 5
    ) -> list[MemoryRecord]:
        """Last N rows excluding the given agent names, chronological."""
        ...

    def last_action(self, agent_name: str | None = None) -> MemoryRecord | None:
        """Most recent row, optionally filtered by agent."""
        ...

    def prune(self) -> int:
        """Delete rows past the retention window. Returns count deleted."""
        ...