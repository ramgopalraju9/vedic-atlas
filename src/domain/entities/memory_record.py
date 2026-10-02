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
