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