"""ToolManifest — the single declarative source of truth for one model-callable tool.

Everything that used to be scattered (description in the skill class, regexes
in the guard, examples in the prompt, hosts in privacy.yaml) is declared once
here, and the rest of the harness is *generated* from it: the prompt block,
the JSON-schema the model is constrained to, the router's trigger patterns,
the guard's claim/intent rules, and the reply mode.

Pure data — no I/O, no regex compilation. Loaded from config/tools/*.yaml by
a ToolManifestStorePort adapter.
"""

from dataclasses import dataclass, field
from typing import Any

REPLY_TEMPLATE = "template"  # reply is built from the tool's own `spoken` text — no 2nd model call
REPLY_LLM = "llm"            # a short narrate-stage model call phrases the result


@dataclass(frozen=True)
class ToolParam:
    """One flat argument of a tool."""

    name: str
    type: str  # "string" | "integer" | "number" | "boolean"
    required: bool = False
    enum: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ToolExample:
    """A worked example: user phrase -> the exact `calls` the model should emit."""

    user: str
    calls: tuple[dict[str, Any], ...]  # each {"tool": name, "args": {...}}


@dataclass(frozen=True)
class ToolManifest:
    name: str                      # tool name the model emits, e.g. "get_weather"
    agent: str                     # owning specialist agent, e.g. "lookup"
    description: str               # one short line — it is paid for in every prompt
    params: tuple[ToolParam, ...]
    examples: tuple[ToolExample, ...] = ()
    triggers: tuple[str, ...] = ()  # regexes: user text that should route to this tool's agent
    required_when: tuple[str, ...] = ()  # regexes: user text for which a call is MANDATORY (facts must come from the tool)
    claims: tuple[str, ...] = ()    # regexes: reply text that asserts this tool's action happened
    reply_mode: str = REPLY_LLM
    cache_ttl_sec: int = 0
    permission_level: str = "notify"
    hosts: tuple[str, ...] = field(default_factory=tuple)  # egress hosts the tool may touch
    requires_online: bool = False
