"""DispatchPolicy — what to do with a ControlDecision. Pure; fails closed.

Table (first match wins):

  invalid output, or calls that name no real tool        -> FAIL_CLOSED  fixed apology, nothing runs
  calls non-empty                                        -> TOOLS        (clarification is ignored)
  ... and several calls with any destructive one         -> CLARIFY      one step at a time
  ... and a destructive call whose target the user never named -> CLARIFY  ask which one; nothing runs
  calls empty, clarification text                        -> CLARIFY      ask, no further model call
  calls empty, needs_live_data is True                   -> REFUSE       fixed refusal, NEVER free chat
  otherwise                                              -> CHAT         (needs_live_data False, or no flag)

The decision itself was made by the model after reading the message (principle P1). The ONE place the message is
consulted is the last veto above: a destructive call is only run if the user's own words name its target
(destructive_policy.target_is_explicit). That is a fail-closed check on the model's output, never a way to pick or
force a tool.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from domain.entities.control_decision import ControlDecision
from domain.entities.tool_manifest import ToolManifest
from domain.policies.destructive_policy import is_destructive, target_is_explicit
from domain.policies.tool_call_schema import MAX_CALLS

REFUSAL_REPLY = "I can't look that up right now."
UNPARSEABLE_REPLY = "Sorry, I didn't catch that. Could you say it again?"
ONE_AT_A_TIME_REPLY = "I'll do that one step at a time. What should I do first?"
TARGET_REQUIRED_REPLY = "Which one do you mean?"


class Route(str, Enum):
    TOOLS = "tools"
    CLARIFY = "clarify"
    CHAT = "chat"
    REFUSE = "refuse"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class Resolution:
    route: Route
    calls: tuple[dict, ...] = ()
    text: str = ""    # the reply for CLARIFY / REFUSE / FAIL_CLOSED


def _usable_calls(calls, manifests: Mapping[str, ToolManifest], max_calls: int) -> list[dict]:
    """Real tools only, object args, exact duplicates dropped, capped. (The grammar already guarantees this
    for the constrained decode; a second protocol or a future bug must not be able to bypass it.)"""
    out: list[dict] = []
    for c in calls:
        if not isinstance(c, dict) or c.get("tool") not in manifests or not isinstance(c.get("args"), dict):
            continue
        item = {"tool": c["tool"], "args": c["args"]}
        if item not in out:
            out.append(item)
    return out[:max_calls]


def resolve(
    decision: ControlDecision, manifests: Mapping[str, ToolManifest], *, max_calls: int = MAX_CALLS, user_message: str | None = None,
) -> Resolution:
    if not decision.valid:
        return Resolution(Route.FAIL_CLOSED, text=UNPARSEABLE_REPLY)

    if decision.calls:
        calls = _usable_calls(decision.calls, manifests, max_calls)
        if not calls:
            return Resolution(Route.FAIL_CLOSED, text=UNPARSEABLE_REPLY)
        if len(calls) > 1 and any(is_destructive(manifests[c["tool"]], c["args"]) for c in calls):
            return Resolution(Route.CLARIFY, text=ONE_AT_A_TIME_REPLY)   # destructive tools never join a multi-call
        if user_message is not None and not all(
            target_is_explicit(manifests[c["tool"]], c["args"], user_message) for c in calls
        ):
            return Resolution(Route.CLARIFY, text=TARGET_REQUIRED_REPLY)   # a guessed or inherited target never runs
        return Resolution(Route.TOOLS, calls=tuple(calls))

    question = (decision.clarification or "").strip()
    if question:
        return Resolution(Route.CLARIFY, text=question)
    if decision.needs_live_data is True:
        return Resolution(Route.REFUSE, text=REFUSAL_REPLY)
    return Resolution(Route.CHAT)
