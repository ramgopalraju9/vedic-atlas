"""AgentContext — request-scoped state that flows through supervisor -> agent -> skill.

Donor: veda/meta/context.py. Changes made during migration:
  - Dropped `image_path` / `image_paths` (vision is out of scope).
  - Dropped `recognized_user` (face recognition is out of scope).
  - Promoted `from_voice` from an implicit key buried in `metadata` to an
    explicit, typed field. It drives the voice brevity rule (1-2 sentence
    replies) in service/voice/brevity_policy.py — that rule was previously
    an undocumented load-bearing invariant; making the field explicit here
    is what makes it testable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class AgentContext:
    """Request-scoped context carrying state through supervisor -> agent -> skill."""

    request_id: str = field(default_factory=lambda: uuid4().hex[:12])
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
    knowledge_context: str = ""

    # Permission state
    pending_approvals: list[dict] = field(default_factory=list)
    approved_actions: list[str] = field(default_factory=list)

    # Free-form extension point for hooks. Anything that needs to become a
    # first-class, testable field (like from_voice was) should be promoted
    # out of here rather than accumulating untyped keys.
    metadata: dict[str, Any] = field(default_factory=dict)

    # Session scope for working state (docs/10). `session_id` is resolved ONCE per turn by the first
    # component that needs it (None until then); `speaker_id` is "" until speaker profiles ship.
    session_id: str | None = None
    speaker_id: str = ""