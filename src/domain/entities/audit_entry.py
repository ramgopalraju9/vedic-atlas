from dataclasses import dataclass, field
from datetime import datetime, timezone
from domain.entities.policy_decision import PolicyDecision

@dataclass
class AuditEntry:
    """Structured audit record for any governed action."""
    event_type: str
    agent: str
    context: dict = field(default_factory=dict)
    decision: PolicyDecision = field(default_factory=PolicyDecision)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hash: str = ""
