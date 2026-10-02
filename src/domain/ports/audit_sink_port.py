from typing import Protocol, runtime_checkable
from domain.entities.audit_entry import AuditEntry

@runtime_checkable
class AuditSinkPort(Protocol):
    """Appends AuditEntry rows to durable, tamper-evident storage."""

    def record(self, entry: AuditEntry) -> AuditEntry:
        """Persist an entry, returning it with 'hash' populated."""
        ...

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        """Most recent entries, newest first - feeds the Privacy panel."""
        ...

    def query(self, event_type: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
        """Recent entries as plain dicts, with optional filters - feeds the governance dashboard."""
        ...

    def stats(self) -> dict:
        """Summary counts (total/allowed/denied) - feeds the governance dashboard."""
        ...

    def verify_chain(self) -> dict:
        """Verify the tamper-evident hash chain end to end."""
        ...
