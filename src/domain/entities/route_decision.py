"""RouteDecision — the answer of a model-based router: which specialist, and what it cost."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    agent: str
    ms: int
    prompt_tokens: int
