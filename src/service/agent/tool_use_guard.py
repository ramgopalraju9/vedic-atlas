"""ToolUseGuard — manifest-driven rules for "did a tool really run?".

Built from the ToolManifests, so adding a tool adds its guard rules with no
code change. Pure: no I/O, no model calls.

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
        self._claims = [r for m in manifests for r in _compile(m.claims)]

    def claims_action(self, text: str) -> bool:
        text = text or ""
        return any(p.search(text) for p in self._claims)
