from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentResult:
    """The output of an agent's execution."""

    agent_name: str
    response: str
    skill_calls: list[dict] = field(default_factory=list)
    delegated_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
