"""Check a labelled sample before it is allowed into training.

Errors keep a sample out; warnings are counted in the build report and sampled for a human to read. The checks reuse the
serving code wherever they can: `dispatch_policy.resolve` is the policy that runs at serving, so a label that the policy
would veto (a delete that names no target, a destructive call in a multi-call, a pronoun as a target) is wrong by definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.entities.control_decision import ControlDecision
from domain.entities.tool_manifest import ToolManifest, ToolParam
from domain.policies.dispatch_policy import Route, resolve
from domain.policies.tool_call_schema import CLARIFICATION_MAX_CHARS, MAX_CALLS

from training.render import canonical_label, manifests as all_manifests, visible_tools
from training.sample import Sample

_STRING_MAX = 200
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WORD = re.compile(r"[a-z0-9']+")
# Free-text arguments should be made of what the user said (or what the conversation already named). Enums, currency codes
# ("rupees" -> INR) and numbers ("forty two" -> 42) are translations, not copies, so they are not checked.
_FREE_TEXT = {
    ("web_search", "query"), ("tasks", "title"), ("remember", "topic"), ("remember", "value"), ("gmail_search", "query"),
    ("gmail_read", "query"), ("gmail_draft", "to"), ("gmail_draft", "subject"), ("calendar_create", "title"), ("app_control", "name"),
}
# Strings from the prompt's own examples. If one shows up in an argument the user never said, the label was copied from the prompt.
LEAK_PHRASES = ("software review", "ten minutes late", "dentist appointment", "renew passport", "buy bread", "match result last night")


@dataclass(frozen=True)
class Issue:
    level: str   # error | warn
    code: str
    message: str


def _type_ok(param: ToolParam, value) -> bool:
    if param.type == "string":
        return isinstance(value, str) and 0 < len(value) <= _STRING_MAX
    if param.type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if param.type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if param.type == "boolean":
        return isinstance(value, bool)
    return False


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) >= 3}


def _expected_route(label: dict) -> Route:
    if label.get("calls"):
        return Route.TOOLS
    if label.get("clarification"):
        return Route.CLARIFY
    return Route.REFUSE if label.get("needs_live_data") else Route.CHAT


def validate(sample: Sample) -> list[Issue]:
    issues: list[Issue] = []
    err = lambda code, msg: issues.append(Issue("error", code, msg))   # noqa: E731
    warn = lambda code, msg: issues.append(Issue("warn", code, msg))   # noqa: E731

    label, user = sample.label, sample.user
    if not isinstance(label, dict) or not isinstance(label.get("needs_live_data"), bool) or not isinstance(label.get("calls"), list):
        return [Issue("error", "shape", "label needs a boolean needs_live_data and a list calls")]
    unknown = set(label) - {"needs_live_data", "calls", "clarification"}
    if unknown:
        err("shape", f"unexpected label keys {sorted(unknown)}")
    if not (user or "").strip() or len(user) > 500:
        err("user", "empty or over 500 characters")
    for h in sample.history:
        if set(h) != {"user", "veda"} or not h["user"] or not h["veda"]:
            err("history", "each earlier exchange needs a user and a veda text")
    if len(sample.history) > 2:
        warn("history", "only the last two exchanges are shown to the router")

    tools = visible_tools(sample) if all(t in all_manifests() for t in (sample.tools or [])) else {}
    if not tools:
        return issues + [Issue("error", "tools", f"unknown tool in the visible set {sample.tools}")]

    calls, live, clar = label["calls"], label["needs_live_data"], label.get("clarification")
    if len(calls) > MAX_CALLS:
        err("calls", f"more than {MAX_CALLS} calls")
    if calls and not live:
        err("flag", "a call means needs_live_data is true")
    if clar:
        if calls:
            err("clarification", "a clarification means no calls")
        if live:
            err("clarification", "a clarification is written with needs_live_data false")
        if len(clar) > CLARIFICATION_MAX_CHARS or len(clar.split()) > 25:
            err("clarification", "one short question please")
    elif "clarification" in label:
        err("clarification", "leave the clarification key out when not asking")

    context_words = _words(user) | _words(sample.active) | {w for h in sample.history for w in _words(h["user"] + " " + h["veda"])}
    seen_tools = []
    for call in calls:
        name = call.get("tool") if isinstance(call, dict) else None
        if name not in tools:
            err("tool", f"{name!r} is not a visible tool")
            continue
        seen_tools.append(name)
        manifest: ToolManifest = tools[name]
        args = call.get("args")
        if not isinstance(args, dict):
            err("args", f"{name}: args must be an object")
            continue
        params = {p.name: p for p in manifest.params}
        for extra in set(args) - set(params):
            err("args", f"{name}: unknown argument {extra!r}")
        for p in manifest.params:
            if p.required and args.get(p.name) in (None, ""):
                err("args", f"{name}: missing required {p.name!r}")
        for key, value in args.items():
            p = params.get(key)
            if p is None:
                continue
            if value is None or not _type_ok(p, value):
                err("args", f"{name}.{key}: {value!r} is not a valid {p.type}")
            elif p.enum and value not in p.enum:
                err("args", f"{name}.{key}: {value!r} not in {list(p.enum)}")
            elif key == "date_offset" and not 0 <= value <= 6:
                err("args", f"{name}.date_offset must be 0..6")
            elif key == "time" and not _TIME.match(value):
                err("args", f"{name}.time must be 24-hour HH:MM")
            elif key == "duration_minutes" and not 5 <= value <= 480:
                err("args", f"{name}.duration_minutes must be 5..480")
            elif isinstance(value, str):
                low = value.lower()
                for phrase in LEAK_PHRASES:
                    if phrase in low and phrase not in " ".join([user, sample.active] + [h["user"] + h["veda"] for h in sample.history]).lower():
                        err("leak", f"{name}.{key}={value!r} was copied from a prompt example")
                if (name, key) in _FREE_TEXT and not value.strip().isdigit() and not (_words(value) & context_words) and "@" not in value:
                    warn("grounding", f"{name}.{key}={value!r} shares no word with what the user said")
        if name == "gmail_send" and "gmail_draft" not in sample.active:
            err("policy", "gmail_send is only valid right after a draft was read back (ACTIVE shows gmail_draft)")
        if name == "tasks" and args.get("action") in ("delete", "complete") and not (args.get("title") or args.get("task_id")):
            err("policy", "tasks delete/complete must name its target")

    if not issues or all(i.level == "warn" for i in issues):
        # the serving policy has the last word: a label it would veto or reroute is wrong
        decision = ControlDecision(calls=tuple(calls), needs_live_data=live, clarification=clar)
        route = resolve(decision, tools, user_message=user).route
        if route is not _expected_route(label):
            err("policy", f"serving would route this as {route.value}, the label intends {_expected_route(label).value}")
        try:
            canonical_label(label, tools)
        except Exception as e:   # noqa: BLE001
            err("serialise", f"label cannot be serialised: {e}")
    return issues
