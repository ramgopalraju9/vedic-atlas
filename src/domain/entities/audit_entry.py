"""AuditEntry — a structured, hash-chained record of a governed action.

Donor: veda/governance/protocol.py (AuditEntry dataclass), copied
verbatim. `hash` is filled in by the audit sink adapter (tpa/governance/
sqlite_audit_sink.py in a later batch), not by whoever constructs the
entry — that's what makes the chain tamper-evident.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from domain.entities.policy_decision import PolicyDecision


@dataclass
class AuditEntry:
    """Structured audit record for any governed action."""

    event_type: str  # "agent_route" | "skill_call" | "inference_call" | "egress" | ...
    agent: str
    action: str
    context: dict = field(default_factory=dict)
    decision: PolicyDecision = field(default_factory=PolicyDecision)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hash: str = ""  # filled by the audit sink adapter, not the caller