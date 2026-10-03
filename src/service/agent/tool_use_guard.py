"""ToolUseGuard — manifest-driven rules for "did a tool really run?".

Built from the ToolManifests, so adding a tool adds its guard rules with no
code change. Pure: no I/O, no model calls.

  required_tools(user_message) — tools whose `required_when` patterns match:
      for these, facts must come from a tool call, so a model answer with no
      call is rejected and the call is forced (constrained retry, minItems=1).
  claims_action(text) — True if a *reply* asserts a tool action/listing
      happened ("Task added", "your tasks are…"). Used to refuse such a claim
      on any path where no tool actually ran.
"""

from __future__ import annotations

import re
from typing import Iterable

from domain.entities.tool_manifest import ToolManifest


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


class ToolUseGuard:
    def __init__(self, manifests: Iterable[ToolManifest]):
        self._required = {m.name: _compile(m.required_when) for m in manifests if m.required_when}
        self._claims = [r for m in manifests for r in _compile(m.claims)]

    def required_tools(self, user_message: str) -> list[str]:
        text = user_message or ""
        return [name for name, pats in self._required.items() if any(p.search(text) for p in pats)]

    def claims_action(self, text: str) -> bool:
        text = text or ""
        return any(p.search(text) for p in self._claims)
