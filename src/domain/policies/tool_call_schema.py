"""ToolCallSchema — builds the JSON-schema a model's tool-call output is constrained to.

With constrained decoding (llama.cpp grammar / Ollama `format`) the model
*cannot* emit malformed JSON, an unknown tool, a bad enum value or an unknown
argument. The schema is derived entirely from the ToolManifests of the tools
visible this turn, so the model only ever sees — and can only emit — its own
agent's tools.

Output shape:  {"calls": [ {"tool": "<name>", "args": {...}}, ... ]}
An empty list means "no tool needed".
"""

from __future__ import annotations

from typing import Any

from domain.entities.tool_manifest import ToolManifest, ToolParam

MAX_CALLS = 3


def _param_schema(p: ToolParam) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": p.type}
    if p.enum:
        schema["enum"] = list(p.enum)
    if p.type == "string":
        schema["maxLength"] = 200
    return schema


def tool_args_schema(manifest: ToolManifest) -> dict[str, Any]:
    props = {p.name: _param_schema(p) for p in manifest.params}
    required = [p.name for p in manifest.params if p.required]
    schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


CONTROL_KEYS = ("needs_live_data", "calls", "clarification")   # emission order; the flag is decoded BEFORE the calls
CLARIFICATION_MAX_CHARS = 200


def build_control_schema(
    manifests: list[ToolManifest], *, max_calls: int = MAX_CALLS, key_order: tuple[str, ...] = CONTROL_KEYS
) -> dict[str, Any]:
    """JSON-schema for the unified control decode:
    `{"needs_live_data": bool, "calls": [...], "clarification": str | null}`.

    `calls` may be empty (minItems 0) and is NEVER forced to hold one: forcing a call is what turned a mention
    of "weather" into a weather answer. llama.cpp emits the properties in the order given, so `key_order` decides
    whether the model commits to `needs_live_data` before or after it picks the calls."""
    if sorted(key_order) != sorted(CONTROL_KEYS):
        raise ValueError(f"key_order must be a permutation of {CONTROL_KEYS}")
    one_of = [
        {
            "type": "object",
            "properties": {"tool": {"const": m.name}, "args": tool_args_schema(m)},
            "required": ["tool", "args"],
            "additionalProperties": False,
        }
        for m in manifests
    ]
    if not one_of:
        calls: dict[str, Any] = {"type": "array", "maxItems": 0}
    else:
        item: dict[str, Any] = one_of[0] if len(one_of) == 1 else {"oneOf": one_of}
        calls = {"type": "array", "items": item, "minItems": 0, "maxItems": max_calls}
    props = {
        "needs_live_data": {"type": "boolean"},
        "calls": calls,
        "clarification": {"type": ["string", "null"], "maxLength": CLARIFICATION_MAX_CHARS},
    }
    return {
        "type": "object",
        "properties": {k: props[k] for k in key_order},
        "required": list(key_order),
        "additionalProperties": False,
    }


