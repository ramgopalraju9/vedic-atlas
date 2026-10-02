"""AgentResult — the output of an agent's execution.

Donor: veda/meta/agent.py (AgentResult only — the BaseAgent ABC that lived
in the same file is a service-layer base class, not domain data; it is
staged separately at service/agent/base_agent.py in a later batch).
"""

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