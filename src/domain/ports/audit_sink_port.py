"""AuditSinkPort — durable, tamper-evident storage for AuditEntry rows.

New port wrapping donor behaviour: veda/governance/audit.py held the audit
persistence logic inline with policy evaluation. Split here per migration
rule 6 (no SQLAlchemy/DB specifics may cross into domain/service) — the
concrete SQLite-backed writer is staged at
tpa/governance/sqlite_audit_sink.py.

Extended during Batch 10 grounding: the donor's `AuditBackend` Protocol
(veda/governance/audit.py) also declares `query`/`stats`/`verify_chain` —
the exact three methods controller/routes/governance.py (Batch 9) already
calls via `hasattr(backend, ...)` duck-typing. Added here so a real
`AuditSinkPort` implementation actually satisfies that dashboard instead
of silently no-op'ing forever.
"""

from typing import Protocol, runtime_checkable

from domain.entities.audit_entry import AuditEntry


@runtime_checkable
class AuditSinkPort(Protocol):
    """Appends AuditEntry rows to durable, tamper-evident storage."""

    def record(self, entry: AuditEntry) -> AuditEntry:
        """Persist an entry, returning it with `hash` populated."""
        ...

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        """Most recent entries, newest first — feeds the Privacy panel."""
        ...

    def query(self, *, event_type: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
        """Recent entries as plain dicts, with optional filters — feeds the governance dashboard."""
        ...

    def stats(self) -> dict:
        """Summary counts (total/allowed/denied) — feeds the governance dashboard."""
        ...

    def verify_chain(self) -> dict:
        """Verify the tamper-evident hash chain end to end."""
        ...