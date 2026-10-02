"""SqliteAuditSink — tamper-evident, hash-chained audit trail on local SQLite.

Donor: veda/governance/audit.py's SQLiteAuditBackend, read in full and
adapted to the `AuditSinkPort` contract (Batch 3, extended Batch 10):
  - `append()` (async, but internally pure sqlite3 sync calls) renamed to
    `record()` and made an honest sync method — matches the Port's
    `def record(self, entry: AuditEntry) -> AuditEntry` signature and
    stops pretending a blocking sqlite3 call is async.
  - Reads/writes `domain.entities.audit_entry.AuditEntry` /
    `domain.entities.policy_decision.PolicyDecision` objects directly
    instead of the donor's dataclass-with-the-same-shape-but-different-
    import-path — same fields, one source of truth.
  - Adds `recent()` (Port-required, donor had no equivalent) by mapping
    stored rows back into AuditEntry objects.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from domain.entities.audit_entry import AuditEntry
from domain.entities.policy_decision import PolicyDecision


class SqliteAuditSink:
    """Tamper-evident SQLite audit log with a SHA-256 Merkle hash chain.

    Each row's hash = SHA-256(previous_hash + row_data), forming an
    append-only chain. Any tampering breaks the chain (see verify_chain()).
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                event_type  TEXT    NOT NULL,
                agent       TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                allowed     INTEGER NOT NULL,
                rule_name   TEXT,
                reason      TEXT,
                context     TEXT,
                hash        TEXT    NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp)")
        self._conn.commit()
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        row = self._conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else "0" * 64

    def _compute_hash(self, data: str) -> str:
        return hashlib.sha256((self._last_hash + data).encode()).hexdigest()

    def record(self, entry: AuditEntry) -> AuditEntry:
        ctx_json = json.dumps(entry.context, default=str)
        allowed_int = int(entry.decision.allowed)
        row_data = f"{entry.timestamp.isoformat()}|{entry.event_type}|{entry.agent}|{entry.action}|{allowed_int}|{ctx_json}"
        entry.hash = self._compute_hash(row_data)
        self._last_hash = entry.hash

        self._conn.execute(
            """
            INSERT INTO audit_log
                (timestamp, event_type, agent, action, allowed, rule_name, reason, context, hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.timestamp.isoformat(), entry.event_type, entry.agent, entry.action,
                allowed_int, entry.decision.rule_name, entry.decision.reason, ctx_json, entry.hash,
            ),
        )
        self._conn.commit()
        return entry

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT timestamp, event_type, agent, action, allowed, rule_name, reason, context, hash "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            AuditEntry(
                event_type=r[1],
                agent=r[2],
                action=r[3],
                context=json.loads(r[7]) if r[7] else {},
                decision=PolicyDecision(allowed=bool(r[4]), rule_name=r[5], reason=r[6] or ""),
                timestamp=datetime.fromisoformat(r[0]),
                hash=r[8],
            )
            for r in rows
        ]

    def query(self, *, event_type: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT id, timestamp, event_type, agent, action, allowed, rule_name, reason, context, hash FROM audit_log"
        conditions: list[str] = []
        params: list[str] = []
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(str(limit))

        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0], "timestamp": r[1], "event_type": r[2], "agent": r[3], "action": r[4],
                "allowed": bool(r[5]), "rule_name": r[6], "reason": r[7],
                "context": json.loads(r[8]) if r[8] else {}, "hash": r[9],
            }
            for r in rows
        ]

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN allowed = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) FROM audit_log"
        ).fetchone()
        return {"total": row[0], "allowed": row[1] or 0, "denied": row[2] or 0}

    def verify_chain(self) -> dict:
        """Verify the hash chain integrity end to end."""
        rows = self._conn.execute(
            "SELECT id, timestamp, event_type, agent, action, allowed, context, hash FROM audit_log ORDER BY id"
        ).fetchall()
        if not rows:
            return {"valid": True, "checked": 0, "broken_at": None}

        prev_hash = "0" * 64
        for row in rows:
            ctx = row[6] or "{}"
            row_data = f"{row[1]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}|{ctx}"
            expected = hashlib.sha256((prev_hash + row_data).encode()).hexdigest()
            if expected != row[7]:
                return {"valid": False, "checked": row[0], "broken_at": row[0]}
            prev_hash = row[7]

        return {"valid": True, "checked": len(rows), "broken_at": None}

    def close(self) -> None:
        self._conn.close()


class NullAuditSink:
    """No-op audit sink — entries are discarded. Used when governance audit is off."""

    def record(self, entry: AuditEntry) -> AuditEntry:
        return entry

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        return []

    def query(self, *, event_type: str | None = None, agent: str | None = None, limit: int = 100) -> list[dict]:
        return []

    def stats(self) -> dict:
        return {"total": 0, "allowed": 0, "denied": 0}

    def verify_chain(self) -> dict:
        return {"valid": True, "checked": 0, "broken_at": None}