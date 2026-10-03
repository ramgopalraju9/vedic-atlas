"""ToolObservation — what a successful online lookup tool hands back.

`text` is the compact, fact-only form the narrate stage may use; `spoken` is a
single natural sentence the tool built itself (the reply for template tools,
and the deterministic fallback when narration is rejected). Both are built
from the provider's data only — nothing here comes from the model.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolObservation:
    text: str
    spoken: str
    source: str = ""     # who answered, e.g. "open-meteo", "tavily"
    as_of: str = ""      # when the underlying data is from, human-readable
    cached: bool = False
    final: bool = False  # `spoken` is the finished reply: no narrate-stage model call needed
    data: dict[str, Any] = field(default_factory=dict)
