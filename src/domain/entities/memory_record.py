"""MemoryRecord — one entry in the cross-agent memory mesh.

Donor: veda/db/agent_memory_repo.py's AgentMemory table, reconstructed as a
pure entity from its actual read-side shape (see veda/agents/llm_agent.py
`_memory_context`, which reads `m['agent_name']`, `m['action']`,
`m.get('context', {})`, `m.get('created_at', '')` off exactly these
fields). The ORM row itself is staged in tpa/persistence/models/ in a
later batch.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MemoryRecord:
    """One recorded action any agent took, visible to every other agent."""

    agent_name: str
    action: str
    context: dict[str, Any] = field(default_factory=dict)
    user_message: str = ""
    created_at: datetime | None = None