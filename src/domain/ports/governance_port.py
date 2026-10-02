from typing import Protocol, runtime_checkable
from domain.entities.audit_entry import AuditEntry
from domain.entities.policy_decision import PolicyDecision

@runtime_checkable
class GovernanceProvider(Protocol):
    """Abstract governance interface."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. 'builtin', 'null')."""
        ...

    def check_action(self, action: str, context: dict) -> PolicyDecision:
        """Evaluate an action against policies."""
        ...

    def check_pattern(self, text: str) -> list[str]:
        """Scan text for blocked patterns. Returns matches."""
        ...

    async def audit(self, entry: AuditEntry) -> None:
        """Record an audit entry to the governance trail."""
        ...

    def is_healthy(self, backend: str) -> bool:
        """Is the given inference backend currently healthy?"""
        ...

    def record_success(self, backend: str) -> None:
        """Record a successful call against a backend."""
        ...

    def record_failure(self, backend: str) -> None:
        """Record a failed call. May trip the breaker."""
        ...
