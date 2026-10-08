"""DestructivePolicy — is this call destructive, and did the user actually name its target? Pure.

A call is destructive if the whole tool is (`destructive: true`) or if any argument holds a value
listed in `destructive_when` (e.g. tasks with action=delete). A per-tool boolean alone is too
coarse: `tasks add` is safe and should inherit context.

`target_is_explicit` is a fail-closed VETO on a model-emitted destructive argument, not a router: it never
chooses, forces or skips a tool for a normal request. If the model says "delete the task titled X" but the
user's own message never mentioned X (or the model gave no target at all), the call is not run and the turn
asks instead. A hallucinated target ("delete it" -> task_id 1, "forget it" -> a guessed fact) is exactly the
wrong-write failure the design must keep near zero. It is the same shape as checking a narrated number
against its sources.
"""

import re
from typing import Any, Mapping

from domain.entities.tool_manifest import ToolManifest

_WORD = re.compile(r"[a-z0-9]+")
_MIN_WORD = 3   # "of", "to", "my" carry no identity; "tv" or "x" fall back to whole-word matching
# A pronoun, ordinal or filler word is never a name. The model emits one when the user said "close it", "delete the
# first one" or "remove that task": the word is trivially present in that very message, so it must not count as the
# user naming a target. A value made ONLY of such words ("first one", "the task") names nothing.
_PLACEHOLDERS = frozenset({"it", "its", "that", "this", "them", "those", "these", "one", "ones", "him", "her", "there", "here", "none", "null", "unknown"})
_GENERIC = _PLACEHOLDERS | frozenset({
    "the", "and", "for", "with", "task", "tasks", "item", "items", "thing", "things", "fact", "note",
    "first", "second", "third", "last", "next", "previous", "other", "another", "please", "all",
})


def is_destructive(manifest: ToolManifest, args: Mapping[str, Any]) -> bool:
    if manifest.destructive:
        return True
    return any(str(args.get(param)) in values for param, values in manifest.destructive_when)


def target_names_nothing(manifest: ToolManifest, args: Mapping[str, Any]) -> bool:
    """True when a target argument the model filled is only a pronoun, ordinal or filler word ("it", "first one",
    "the task"). Applies to EVERY call with `target_params`, not just destructive ones: `tasks complete title="it"`
    would match any task containing those letters. A real name inherited from the conversation ("milk") passes;
    unlike `target_is_explicit` this never looks at the user's message."""
    for param in manifest.target_params:
        value = str(args.get(param) or "").strip().lower()
        if not value:
            continue
        if value in _PLACEHOLDERS:
            return True
        raw = _WORD.findall(value)
        if raw and not [w for w in raw if len(w) >= _MIN_WORD and w not in _GENERIC] and any(len(w) >= _MIN_WORD for w in raw):
            return True
    return False


def target_is_explicit(manifest: ToolManifest, args: Mapping[str, Any], user_message: str) -> bool:
    """True when the call may proceed: it is not destructive, the tool has no target rule, or the user's own
    message names (a significant word of) the target the model chose. False = ask instead."""
    if not is_destructive(manifest, args) or not manifest.target_params:
        return True
    message = (user_message or "").lower()
    for param in manifest.target_params:
        value = str(args.get(param) or "").strip().lower()
        if not value or value in _PLACEHOLDERS:
            continue
        raw = _WORD.findall(value)
        words = [w for w in raw if len(w) >= _MIN_WORD and w not in _GENERIC]
        if words:
            if any(w in message for w in words):
                return True
            continue
        if any(len(w) >= _MIN_WORD for w in raw):
            continue                                   # only generic words ("the first one"): names nothing
        if re.search(r"(?<![a-z0-9])" + re.escape(value) + r"(?![a-z0-9])", message):   # short value: "x", "tv"
            return True
    return False
