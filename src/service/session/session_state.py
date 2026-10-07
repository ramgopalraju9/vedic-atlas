"""SessionStateService — records and reads the working state for a turn.

Phase 1 (shadow): written after successful tool calls and only *logged*; nothing reads it
into a prompt yet. Every method swallows persistence errors — state is an optimisation, and a
failure here must never take a turn down.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping, Sequence

from core.logging_config import logger
from domain.entities.session_context import SessionContext
from domain.entities.tool_manifest import ToolManifest
from domain.policies.session_state_policy import DEFAULT_TTL_SEC, active_line, state_after_call
from domain.ports.session_context_port import SessionContextPort


class SessionStateService:
    def __init__(
        self,
        repo: SessionContextPort,
        manifests: Mapping[str, ToolManifest],
        *,
        ttl_sec: int = DEFAULT_TTL_SEC,
        now: Callable[[], datetime] = datetime.now,
    ):
        self._repo = repo
        self._manifests = manifests
        self._ttl_sec = ttl_sec
        self._now = now

    def get(self, session_id: str, speaker_id: str = "") -> SessionContext | None:
        try:
            return self._repo.get(session_id, speaker_id, self._now())
        except Exception as e:
            logger.warning(f"[session-state] read failed: {e}")
            return None

    def active_line(self, session_id: str, speaker_id: str = "") -> str:
        return active_line(self.get(session_id, speaker_id), self._now())

    def render_active(self, state: SessionContext | None) -> str:
        """The `ACTIVE:` line for state already read this turn (one read per turn, one clock)."""
        return active_line(state, self._now())

    def purge_expired(self) -> int:
        try:
            return self._repo.purge_expired(self._now())
        except Exception as e:
            logger.warning(f"[session-state] purge failed: {e}")
            return 0

    def record_success(self, session_id: str, speaker_id: str, calls: Sequence) -> SessionContext | None:
        """Remember the LAST successful call of the turn (`calls` are ExecutedCall-shaped: .tool, .args, .ok).
        Failed calls never write; a destructive last call writes nothing and does not fall back to an older call."""
        now = self._now()
        for call in reversed(list(calls)):
            manifest = self._manifests.get(call.tool)
            if manifest is None or not call.ok:
                continue
            state = state_after_call(
                manifest, call.args, session_id=session_id, speaker_id=speaker_id, now=now, ttl_sec=self._ttl_sec,
            )
            if state is None:
                return None
            try:
                self._repo.upsert(state)
            except Exception as e:
                logger.warning(f"[session-state] write failed: {e}")
                return None
            return state
        return None
