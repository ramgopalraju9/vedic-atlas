"""Record - the write-side use case for the cross-agent memory mesh.

Donor: veda/db/agent_memory_repo.py's `record`/`prune`, read in full. Those
are exposed directly as MemoryRepositoryPort methods (Batch 3) - there's
no extra orchestration to add on write, so this module is a thin,
explicit use-case wrapper kept separate from CrossAgentContext (the
read-side) per the target tree's own naming (`service/memory/record.py`
vs `cross_agent_context.py`), rather than merging both directions into
one class.
"""

from domain.ports.memory_repository_port import MemoryRepositoryPort


class Record:
    """Write-side use case: record an agent's action to the shared memory mesh."""

    def __init__(self, memory: MemoryRepositoryPort):
        self.memory = memory

    def record(self, self_agent_name: str, action: str, context: dict | None = None, user_message: str = "") -> int:
        return self.memory.record(agent_name=self_agent_name, action=action, context=context or {}, user_message=user_message)

    def prune(self) -> int:
        return self.memory.prune()