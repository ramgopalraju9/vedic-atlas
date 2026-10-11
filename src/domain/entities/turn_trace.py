"""TurnTrace — one tool-using turn, recorded so "did the call really happen?" is answerable.

Pure data. Persisted by a TraceRepositoryPort adapter and shown by
`veda trace` / GET /api/trace. Holds what the user said, what the model
decided, every tool call with its real result, how long each stage took, and
how big each prompt was.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TurnTrace:
    request_id: str
    agent: str
    user_message: str
    reply: str
    decided: str                      # "tool" | "no-tool"
    created_at: datetime = field(default_factory=datetime.now)
    forced: bool = False              # the call was forced by the required-tool guard
    narrated: bool = False            # the reply came from a narrate-stage model call
    total_ms: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)   # {tool, args, ok, ms, result, error}
    prompt_tokens: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Orchestrator observability (docs/10). Inert until the orchestrator fills them.
    state_used: bool = False                 # an ACTIVE: session-state line was in the control prompt
    slots_inherited: list[str] = field(default_factory=list)   # arg names filled from session state
    needs_live_data: bool | None = None      # the control decode's flag; None = not a control decode
    clarified: bool = False                  # the reply was a clarification question
    prefix_cache_hit: bool | None = None     # the backend reused the cached prompt prefix; None = unknown
    id: int | None = None
