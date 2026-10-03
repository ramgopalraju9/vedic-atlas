"""PromptComposer — assembles each stage's prompt under a hard token budget.

The model is a small quantized one on CPU, so prompts are built from the
*minimum* the stage needs:

  Stage A (call):     persona_lite + only this agent's tools + their examples
                      + last 2 turns + the user message
  Stage B (narrate):  persona_lite + narrate rules + the question + the (capped)
                      tool result. No tools, no history.

Static text (persona, tool block, examples) always comes first and is
byte-identical turn to turn, so the backend's KV prefix cache is reused; only
the volatile tail (date, history, user message, tool result) changes.

If a stage is over budget the composer trims in a fixed order — oldest
history first, then the tool result / user message — and reports what it
trimmed so the trace can show it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

from domain.entities.conversation import Turn
from domain.entities.tool_manifest import ToolManifest, ToolParam
from domain.policies.token_budget_policy import PromptBudgets, estimate_tokens
from domain.ports.tool_manifest_store_port import PromptStorePort

_TURN_CHARS = 200        # a history turn is cut to this many characters
_USER_MSG_CHARS = 500


@dataclass
class ComposedPrompt:
    system: str
    prompt: str
    tokens: int
    sections: dict[str, int] = field(default_factory=dict)
    trimmed: list[str] = field(default_factory=list)


class PromptComposer:
    def __init__(
        self,
        prompts: PromptStorePort,
        manifests: Sequence[ToolManifest],
        budgets: PromptBudgets | None = None,
        count_tokens: Callable[[str], int] = estimate_tokens,
        now: Callable[[], datetime] = datetime.now,
    ):
        self._prompts = prompts
        self._manifests = {m.name: m for m in manifests}
        self.budgets = budgets or PromptBudgets()
        self._count = count_tokens
        self._now = now
        self._static_cache: dict[tuple[str, ...], str] = {}

    # ---- lookups ----------------------------------------------------------

    def manifests_for(self, tool_names: Sequence[str]) -> list[ToolManifest]:
        return [self._manifests[n] for n in tool_names if n in self._manifests]

    def tools_of_agent(self, agent: str) -> list[str]:
        return [m.name for m in self._manifests.values() if m.agent == agent]

    # ---- rendering helpers -----------------------------------------------

    @staticmethod
    def _signature(m: ToolManifest) -> str:
        def one(p: ToolParam) -> str:
            kind = "|".join(p.enum) if p.enum else p.type
            return f"{p.name}{'' if p.required else '?'}: {kind}"

        return f"- {m.name}({', '.join(one(p) for p in m.params)}) - {m.description}"

    @staticmethod
    def _example(m: ToolManifest) -> list[str]:
        lines = []
        for ex in m.examples:
            payload = json.dumps({"calls": list(ex.calls)}, separators=(",", ":"), ensure_ascii=False)
            lines.append(f"User: {ex.user}\n{payload}")
        return lines

    def _static_call_text(self, tool_names: Sequence[str]) -> str:
        key = tuple(tool_names)
        if key not in self._static_cache:
            manifests = self.manifests_for(tool_names)
            tools = "\n".join(self._signature(m) for m in manifests)
            examples = "\n".join(line for m in manifests for line in self._example(m))
            body = self._prompts.get("call_stage").replace("<<tools>>", tools).replace("<<examples>>", examples)
            self._static_cache[key] = f"{self._prompts.get('persona_lite')}\n\n{body}"
        return self._static_cache[key]

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = " ".join((text or "").split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _history_lines(self, history: Sequence[Turn], keep: int) -> list[str]:
        lines = []
        for t in list(history)[-keep:] if keep > 0 else []:
            content = self._clip(t.content, _TURN_CHARS)
            if content:
                lines.append(f"{'User' if t.role == 'user' else 'Veda'}: {content}")
        return lines

    # ---- stages -----------------------------------------------------------

    def call_stage(self, tool_names: Sequence[str], user_message: str, history: Sequence[Turn] = ()) -> ComposedPrompt:
        system = self._static_call_text(tool_names)
        today = f"TODAY: {self._now().strftime('%A %d %b %Y, %H:%M')}."
        user_line = f"USER: {self._clip(user_message, _USER_MSG_CHARS)}"
        keep = self.budgets.call_history_turns
        trimmed: list[str] = []

        while True:
            hist = self._history_lines(history, keep)
            volatile = "\n".join(([("RECENT:\n" + "\n".join(hist))] if hist else []) + [today, user_line])
            total = self._count(system) + self._count(volatile)
            if total <= self.budgets.call or keep == 0:
                break
            keep -= 1
            trimmed.append("history_turn")

        sections = {
            "static": self._count(system),
            "history": self._count("\n".join(hist)) if hist else 0,
            "user": self._count(user_line),
        }
        return ComposedPrompt(system=system, prompt=volatile, tokens=total, sections=sections, trimmed=trimmed)

    def narrate_stage(self, user_message: str, tool_results: Sequence[str]) -> ComposedPrompt:
        system = f"{self._prompts.get('persona_lite')}\n\n{self._prompts.get('narrate')}"
        result = "\n".join(r.strip() for r in tool_results if r and r.strip())
        trimmed: list[str] = []
        cap = self.budgets.observation_max
        if self._count(result) > cap:
            result = self._truncate_tokens(result, cap)
            trimmed.append("tool_result")
        question = self._clip(user_message, _USER_MSG_CHARS)
        today = f"TODAY: {self._now().strftime('%d %b %Y')}"
        prompt = f"{today}\nQUESTION: {question}\nTOOL RESULT:\n{result}\n\nAnswer:"
        total = self._count(system) + self._count(prompt)
        if total > self.budgets.narrate:
            trimmed.append("tool_result")
            target = max(50, self._count(result) - (total - self.budgets.narrate))
            while True:  # the ellipsis suffix can cost a token, so re-check until it fits
                result = self._truncate_tokens(result, target)
                prompt = f"{today}\nQUESTION: {question}\nTOOL RESULT:\n{result}\n\nAnswer:"
                total = self._count(system) + self._count(prompt)
                if total <= self.budgets.narrate or target <= 50:
                    break
                target -= 8
        sections = {"static": self._count(system), "tool_result": self._count(result)}
        return ComposedPrompt(system=system, prompt=prompt, tokens=total, sections=sections, trimmed=trimmed)

    def _truncate_tokens(self, text: str, max_tokens: int) -> str:
        """Cut `text` so it fits `max_tokens` (binary search on characters)."""
        if self._count(text) <= max_tokens:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._count(text[:mid]) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + " …"
