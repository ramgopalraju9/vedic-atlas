"""Pydantic shape of a config/tools/<name>.yaml manifest.

Validation lives here (schemas layer) so a bad manifest fails loudly at boot
instead of mis-prompting the model at runtime. `to_domain()` converts to the
pure ToolManifest entity the rest of the harness uses.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from domain.entities.tool_manifest import REPLY_LLM, REPLY_TEMPLATE, ToolExample, ToolManifest, ToolParam

MAX_DESCRIPTION_WORDS = 25   # a description is paid for in every prompt
MAX_EXAMPLES = 6


class ToolParamSchema(BaseModel):
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = False
    enum: list[str] = Field(default_factory=list)
    description: str = ""


class ToolExampleSchema(BaseModel):
    user: str
    calls: list[dict[str, Any]]

    @field_validator("calls")
    @classmethod
    def _calls_shape(cls, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for c in calls:
            if set(c) != {"tool", "args"} or not isinstance(c["args"], dict):
                raise ValueError("each call must be {tool: <name>, args: {...}}")
        return calls


class ToolManifestSchema(BaseModel):
    name: str
    agent: str
    description: str
    params: dict[str, ToolParamSchema] = Field(default_factory=dict)
    examples: list[ToolExampleSchema] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    required_when: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    reply_mode: Literal["template", "llm"] = REPLY_LLM
    cache_ttl_sec: int = Field(default=0, ge=0)
    permission_level: Literal["auto", "notify", "approve"] = "notify"
    hosts: list[str] = Field(default_factory=list)
    requires_online: bool = False

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError("name must be lower_snake_case")
        return v

    @field_validator("description")
    @classmethod
    def _description_short(cls, v: str) -> str:
        if len(v.split()) > MAX_DESCRIPTION_WORDS:
            raise ValueError(f"description must be <= {MAX_DESCRIPTION_WORDS} words (it is sent in every prompt)")
        return v.strip()

    @field_validator("triggers", "required_when", "claims")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            re.compile(p)
        return patterns

    @model_validator(mode="after")
    def _check(self) -> "ToolManifestSchema":
        if len(self.examples) > MAX_EXAMPLES:
            raise ValueError(f"at most {MAX_EXAMPLES} examples (each costs prompt tokens)")
        for ex in self.examples:
            for c in ex.calls:
                if c["tool"] != self.name:
                    raise ValueError(f"example calls tool '{c['tool']}', expected '{self.name}'")
                unknown = set(c["args"]) - set(self.params)
                if unknown:
                    raise ValueError(f"example uses unknown params {sorted(unknown)}")
        return self

    def to_domain(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            agent=self.agent,
            description=self.description,
            params=tuple(
                ToolParam(name=n, type=p.type, required=p.required, enum=tuple(p.enum), description=p.description)
                for n, p in self.params.items()
            ),
            examples=tuple(ToolExample(user=e.user, calls=tuple(e.calls)) for e in self.examples),
            triggers=tuple(self.triggers),
            required_when=tuple(self.required_when),
            claims=tuple(self.claims),
            reply_mode=REPLY_TEMPLATE if self.reply_mode == "template" else REPLY_LLM,
            cache_ttl_sec=self.cache_ttl_sec,
            permission_level=self.permission_level,
            hosts=tuple(self.hosts),
            requires_online=self.requires_online,
        )
