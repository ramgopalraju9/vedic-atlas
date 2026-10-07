"""SessionStatePolicy — the rules for what session state may be written and how it is shown.

Pure. Time is passed in. The orchestrator (Phase 3) renders `active_line` into the *volatile*
tail of the control prompt; Phase 1 only logs it (shadow mode).
"""

from datetime import datetime, timedelta
from typing import Any, Mapping

from domain.entities.session_context import SessionContext
from domain.entities.tool_manifest import ToolManifest
from domain.policies.destructive_policy import is_destructive

DEFAULT_TTL_SEC = 900
_MAX_SLOT_CHARS = 60   # keeps ACTIVE: near ~25 tokens


def is_expired(state: SessionContext, now: datetime) -> bool:
    return state.expires_at <= now


def usable_slots(args: Mapping[str, Any]) -> dict[str, Any]:
    """Drop empty values: "" / None carry no information to inherit (and "" means "home" to the place resolver)."""
    return {k: v for k, v in args.items() if v not in (None, "")}


def state_after_call(
    manifest: ToolManifest,
    args: Mapping[str, Any],
    *,
    session_id: str,
    speaker_id: str,
    now: datetime,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> SessionContext | None:
    """The state to store after a SUCCESSFUL call, or None when this call must not be remembered."""
    if is_destructive(manifest, args):
        return None
    return SessionContext(
        session_id=session_id, speaker_id=speaker_id, tool=manifest.name, slots=usable_slots(args),
        updated_at=now, expires_at=now + timedelta(seconds=ttl_sec),
    )


def _age(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    return f"{int(seconds // 60)} min ago"


def active_line(state: SessionContext | None, now: datetime) -> str:
    """`ACTIVE: get_weather | place=Tokyo | 3 min ago`, or "" when there is nothing fresh to show."""
    if state is None or is_expired(state, now):
        return ""
    slots = " ".join(f"{k}={str(v)[:_MAX_SLOT_CHARS]}" for k, v in state.slots.items())
    parts = [state.tool] + ([slots] if slots else []) + [_age((now - state.updated_at).total_seconds())]
    return "ACTIVE: " + " | ".join(parts)
