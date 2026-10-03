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


def build_route_schema(agent_names: list[str]) -> dict[str, Any]:
    """JSON-schema for the router's `{"agent": "<name>"}` answer: `agent` can only be a real agent."""
    return {
        "type": "object",
        "properties": {"agent": {"enum": list(agent_names)}},
        "required": ["agent"],
        "additionalProperties": False,
    }


def build_call_schema(manifests: list[ToolManifest], *, min_calls: int = 0, max_calls: int = MAX_CALLS) -> dict[str, Any]:
    """JSON-schema for `{"calls": [...]}` over the given tools."""
    one_of = [
        {
            "type": "object",
            "properties": {"tool": {"const": m.name}, "args": tool_args_schema(m)},
            "required": ["tool", "args"],
            "additionalProperties": False,
        }
        for m in manifests
    ]
    item: dict[str, Any] = one_of[0] if len(one_of) == 1 else {"oneOf": one_of}
    return {
        "type": "object",
        "properties": {"calls": {"type": "array", "items": item, "minItems": min_calls, "maxItems": max_calls}},
        "required": ["calls"],
        "additionalProperties": False,
    }
