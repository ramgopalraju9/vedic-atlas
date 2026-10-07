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
_MIN_WORD = 3   # "of", "to", "my" carry no identity; "tv" or "x" fall back to whole-value matching


def is_destructive(manifest: ToolManifest, args: Mapping[str, Any]) -> bool:
    if manifest.destructive:
        return True
    return any(str(args.get(param)) in values for param, values in manifest.destructive_when)


def target_is_explicit(manifest: ToolManifest, args: Mapping[str, Any], user_message: str) -> bool:
    """True when the call may proceed: it is not destructive, the tool has no target rule, or the user's own
    message names (a significant word of) the target the model chose. False = ask instead."""
    if not is_destructive(manifest, args) or not manifest.target_params:
        return True
    message = (user_message or "").lower()
    for param in manifest.target_params:
        value = str(args.get(param) or "").strip().lower()
        if not value:
            continue
        words = [w for w in _WORD.findall(value) if len(w) >= _MIN_WORD]
        if (words and any(w in message for w in words)) or (not words and value in message):
            return True
    return False
