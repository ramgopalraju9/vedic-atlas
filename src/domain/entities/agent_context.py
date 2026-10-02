from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

@dataclass
class AgentContext:
    """Request-scoped context carrying state through supervisor -> agent -> skill."""

    request_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    user_message: str = ""
    system_context: str = ""

    # True when this turn originated from the voice pipeline rather than the
    # UI or CLI. Consumed by the brevity policy to keep spoken replies short.
    from_voice: bool = False

    # Agent chain tracking
    agent_chain: list[str] = field(default_factory=list)
    current_agent: str = ""

    # Accumulated skill results in this request
    skill_results: list[dict] = field(default_factory=list)

    # Conversation + knowledge references
    conversation_history: list[dict] = field(default_factory=list)
    conversation_context: str = ""

    # Permission state
    pending_approvals: list[dict] = field(default_factory=list)
    approved_actions: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)
