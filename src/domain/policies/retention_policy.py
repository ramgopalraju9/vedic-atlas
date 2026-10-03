"""RetentionPolicy — how long different kinds of stored data live.

Two distinct rules, from two different sources — do not conflate them:

1. Cross-agent memory mesh (donor: veda/db/agent_memory_repo.py's
   `RETENTION_DAYS = 10`, read in full — the donor pruned rows older than
   this on every write). This is internal bookkeeping the user never sees
   directly, so a rolling expiry is fine.

2. User-facing remembered facts (Memory & Recall, Epic 4) — per
   docs/roadmap/adr/ADR-006-memory-retention.md, these have NO auto-expiry.
   A fact lives until the user says "forget X" or uses the Memory panel's
   delete action. Predictability was the explicit reason: a fact silently
   vanishing after N days breaks the user's trust that the companion
   actually remembers what they told it.
"""

from datetime import datetime, timedelta

AGENT_MEMORY_RETENTION_DAYS = 10

# Completed to-do items are kept briefly (so "undo" and "what did I finish"
# still work), then purged. Pending tasks never expire.
COMPLETED_TASK_RETENTION_DAYS = 7

# Tool-turn traces are a debugging aid ("did the call really happen?"), not user data.
TURN_TRACE_RETENTION_DAYS = 14


def is_expired_agent_memory(created_at: datetime, now: datetime) -> bool:
    """True if a cross-agent memory-mesh row is past its retention window."""
    return (now - created_at) > timedelta(days=AGENT_MEMORY_RETENTION_DAYS)


def is_expired_user_fact(created_at: datetime, now: datetime) -> bool:
    """User-facing facts never auto-expire — see ADR-006. Always False."""
    return False