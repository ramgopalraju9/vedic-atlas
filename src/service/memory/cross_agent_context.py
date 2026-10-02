"""CrossAgentContext — read-side use cases over the memory mesh.

Donor: veda/db/agent_memory_repo.py, read in full (Batch 3 grounding).
Only the read-side helpers land here; `record`/`prune` are on the
MemoryRepositoryPort itself (the repository IS the write side — there's
no extra use-case logic layered over `record()`). `last_specialist` and
`last_project` are donor-specific supervisor helpers built on the port's
primitives; `last_specialist`'s intent is now inlined directly into
service/agent/supervisor.py's fallback (simpler: `last_action(agent_name="supervisor")`
does the same job with less bespoke logic). `last_project` had no
confirmed caller found during this migration — omitted rather than
invented a use for it.
"""

from domain.entities.memory_record import MemoryRecord
from domain.ports.memory_repository_port import MemoryRepositoryPort


class CrossAgentContext:
    """Formats recent cross-agent activity for injection into a prompt."""

    def __init__(self, memory: MemoryRepositoryPort):
        self.memory = memory

    def recent_activity(self, exclude_agent: str, limit: int = 5) -> list[MemoryRecord]:
        return self.memory.recent_cross_agent(exclude={exclude_agent}, limit=limit)

    def format_for_prompt(self, exclude_agent: str, limit: int = 5) -> str:
        records = self.recent_activity(exclude_agent, limit)
        if not records:
            return ""
        lines = []
        for m in records:
            ctx_str = ", ".join(f"{k}={v}" for k, v in m.context.items()) if m.context else ""
            ts = m.created_at.isoformat() if m.created_at else ""
            lines.append(f"- [{m.agent_name}] {m.action}" + (f" ({ctx_str})" if ctx_str else "") + (f" @ {ts}" if ts else ""))
        return "RECENT AGENT ACTIVITY:\n" + "\n".join(lines)