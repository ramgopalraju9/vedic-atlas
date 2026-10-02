"""Explicit mappers – SQLAlchemy ORM rows <-> domain entities.

* New file, no donor equivalent (the donor read ORM rows directly into
dicts inline inside the repository methods – `_turn_to_dict`,
`_summary_to_dict` in veda/db/conversation_repo.py). Split out per
migration rule 7: "no SQLAlchemy type may cross into service/" – these
functions are the one place that translation happens, so every
repository method can return domain entities without leaking a Mapped
type anywhere near the service layer.
"""

import json

from domain.entities.conversation import ConversationSummary, Turn
from domain.entities.memory_record import MemoryRecord
from tpa.persistence.models.agent_memory import AgentMemoryRow
from tpa.persistence.models.conversation import ConversationSummaryRow, ConversationTurnRow


def turn_row_to_entity(row: ConversationTurnRow) -> Turn:
    return Turn(
        id=row.id,
        session_id=row.session_id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
        summarized=row.summarized,
    )


def summary_row_to_entity(row: ConversationSummaryRow) -> ConversationSummary:
    return ConversationSummary(
        id=row.id,
        session_id=row.session_id,
        from_ts=row.from_ts,
        to_ts=row.to_ts,
        content=row.content,
        turn_count=row.turn_count,
        created_at=row.created_at,
    )


def memory_row_to_entity(row: AgentMemoryRow) -> MemoryRecord:
    try:
        context = json.loads(row.context_json)
    except (json.JSONDecodeError, TypeError):
        context = {}
    return MemoryRecord(
        agent_name=row.agent_name,
        action=row.action,
        context=context,
        user_message=row.user_message,
        created_at=row.created_at,
    )
