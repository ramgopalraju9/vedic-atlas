"""GovernanceProvider — policy enforcement, pluggable per environment.

Donor: veda/governance/protocol.py, read in full and copied verbatim
except PolicyDecision/AuditEntry now live in domain/entities (so other
ports can depend on them without importing this whole module).

Correction (2026-09-22): the first read of this donor file (55 of the
real 70 lines) missed four methods entirely — `audit`, `is_healthy`,
`record_success`, `record_failure`. The last three are the circuit
breaker that `service/inference/graceful_degradation.py` already calls
via its `on_success`/`on_failure` constructor callbacks; adding them here
makes that dependency an explicit part of the contract instead of an
untyped callable.
"""

from typing import Protocol, runtime_checkable

from domain.entities.audit_entry import AuditEntry
from domain.entities.policy_decision import PolicyDecision


@runtime_checkable
class GovernanceProvider(Protocol):
    """Abstract governance interface — same role as InferencePort for the brain."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. "builtin", "null")."""
        ...

    def check_action(self, action: str, context: dict) -> PolicyDecision:
        """Evaluate an action against policies. Sync, <1ms."""
        ...

    def check_pattern(self, text: str) -> list[str]:
        """Scan text for blocked patterns (PII, credentials). Returns matches."""
        ...

    async def audit(self, entry: AuditEntry) -> None:
        """Record an audit entry to the governance trail."""
        ...

    def is_healthy(self, backend: str) -> bool:
        """Circuit breaker: is the given inference backend currently healthy?"""
        ...

    def record_success(self, backend: str) -> None:
        """Circuit breaker: record a successful call against a backend."""
        ...

    def record_failure(self, backend: str) -> None:
        """Circuit breaker: record a failed call. May trip the breaker."""
        ...