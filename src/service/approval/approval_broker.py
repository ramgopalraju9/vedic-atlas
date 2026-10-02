"""ApprovalBroker — async coordinator for gated skill approvals.

Donor: veda/code/approval/broker.py's ApprovalBroker, read in full and
substantially adapted:
  - Renamed `tool_name`/`tool_input` -> `action_name`/`arguments`
    throughout, matching domain.entities.approval_request.ApprovalRequest
    (whose fields were already renamed in Batch 2 for the same reason —
    Claude/Copilot tool-call vocabulary doesn't apply once those clients
    are gone).
  - DROPPED `auto_allow`/`DEFAULT_AUTO_ALLOW` entirely. In the donor this
    was a set of Claude/Copilot tool names ("Read", "Grep", "WebSearch")
    that bypassed the MCP permission prompt. That mechanism doesn't exist
    anymore — the equivalent concept is PermissionLevel.AUTO, already
    enforced upstream by PermissionManager before an ApprovalRequest is
    ever created. Keeping both would mean two competing "skip approval"
    paths.
  - DROPPED the module-level `_broker` singleton and `get_broker()`/
    `set_broker()`. Per the migration contract's own rule 4 and the
    donor's own comment ("Used by app bootstrap to inject the
    bus-connected broker"), this singleton existed ONLY because a Claude
    CLI subprocess (via the MCP permission-prompt tool) couldn't reach
    `app.state`. There is no such subprocess in this build — the broker
    is constructor-injected from server.py like everything else.
  - Uses `ApprovalRequest` (domain entity) for the public-facing pending
    list; the `asyncio.Future` needed to actually wait for a decision is
    kept in a private dict here, NOT bolted onto the domain entity (a
    domain entity is plain data, per the Batch 2 decision on this exact
    point).
  - `yolo` enable/disable/idle-timeout and voice yes/no resolution are
    kept — both are genuinely generic UX features, not Claude-specific.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from domain.entities.approval_request import ApprovalRequest
from domain.events.ambient_event import AmbientEvent
from domain.events.event_kind import EventKind
from domain.ports.event_publisher_port import EventPublisherPort
from domain.value_objects.urgency import Urgency
from core.logging_config import logger

DEFAULT_APPROVAL_TIMEOUT_SEC = 60
DEFAULT_YOLO_IDLE_SEC = 30 * 60
DEFAULT_VOICE_WINDOW_SEC = 20

_YES_WORDS = {"yes", "yeah", "yep", "yup", "y", "sure", "go", "proceed", "approve", "approved", "ok", "okay", "do it", "go ahead", "do that", "allow", "allowed"}
_NO_WORDS = {"no", "nope", "nah", "n", "stop", "cancel", "deny", "denied", "don't", "dont", "skip", "reject", "block", "not now"}

_YOLO_ON_PATTERNS = [
    re.compile(r"\byou\s+have\s+(all\s+)?(the\s+)?approvals?\b", re.IGNORECASE),
    re.compile(r"\b(auto[\s-]?approve(\s+everything)?)\b", re.IGNORECASE),
    re.compile(r"\b(no\s+need\s+to\s+ask)\b", re.IGNORECASE),
    re.compile(r"\b(yolo(\s+mode)?)\b", re.IGNORECASE),
    re.compile(r"\b(full\s+access)\b", re.IGNORECASE),
]
_YOLO_OFF_PATTERNS = [
    re.compile(r"\b(ask\s+me\s+(again|each\s+time))\b", re.IGNORECASE),
    re.compile(r"\b(stop\s+auto[\s-]?approv\w*)\b", re.IGNORECASE),
    re.compile(r"\b(turn\s+off\s+yolo)\b", re.IGNORECASE),
    re.compile(r"\b(disable\s+(auto\s+)?approvals?)\b", re.IGNORECASE),
]


class ApprovalBroker:
    def __init__(
        self,
        bus: EventPublisherPort | None = None,
        *,
        approval_timeout_sec: int = DEFAULT_APPROVAL_TIMEOUT_SEC,
        yolo_idle_sec: int = DEFAULT_YOLO_IDLE_SEC,
        voice_window_sec: int = DEFAULT_VOICE_WINDOW_SEC,
    ):
        self.bus = bus
        self.approval_timeout_sec = approval_timeout_sec
        self.yolo_idle_sec = yolo_idle_sec
        self.voice_window_sec = voice_window_sec

        self._pending: dict[str, ApprovalRequest] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._yolo_enabled = False
        self._last_activity: datetime | None = None

    # --- yolo ---------------------------------------------------------

    def set_yolo(self, enabled: bool) -> None:
        now = datetime.now(timezone.utc)
        self._yolo_enabled = bool(enabled)
        self._last_activity = now if enabled else None
        logger.info(f"[approval] yolo -> {self._yolo_enabled}")

    def note_activity(self) -> None:
        self._last_activity = datetime.now(timezone.utc)

    def is_yolo(self) -> bool:
        if not self._yolo_enabled:
            return False
        now = datetime.now(timezone.utc)
        if self._last_activity is None:
            self._last_activity = now
        if (now - self._last_activity).total_seconds() > self.yolo_idle_sec:
            logger.info("[approval] yolo expired on idle")
            self.set_yolo(False)
            return False
        return True

    @classmethod
    def detect_yolo_command(cls, text: str) -> str | None:
        """Return 'on' / 'off' / None for yolo-toggle phrases in user text."""
        if not text:
            return None
        for p in _YOLO_OFF_PATTERNS:
            if p.search(text):
                return "off"
        for p in _YOLO_ON_PATTERNS:
            if p.search(text):
                return "on"
        return None

    # --- approval flow ------------------------------------------------

    async def request_approval(self, action_name: str, arguments: dict[str, Any], reason: str = "") -> tuple[bool, str]:
        """Never raises — timeout becomes a deny."""
        self.note_activity()

        if self.is_yolo():
            logger.info(f"[approval] yolo-allow {action_name}")
            return True, "yolo mode"

        request_id = uuid.uuid4().hex[:10]
        req = ApprovalRequest(
            request_id=request_id,
            action_name=action_name,
            arguments=arguments,
            reason=reason,
            created_at=datetime.now(timezone.utc),
            timeout_sec=self.approval_timeout_sec,
        )
        self._pending[request_id] = req
        self._futures[request_id] = asyncio.get_event_loop().create_future()

        if self.bus is not None:
            try:
                await self.bus.publish(
                    AmbientEvent(
                        kind=EventKind.APPROVAL_REQUEST,
                        description=self.summarize(req),
                        urgency=Urgency.HIGH,
                        source="approval",
                        dedupe_key=f"approval:{request_id}",
                        payload={"request_id": request_id, "action_name": action_name, "arguments": arguments, "reason": reason},
                    )
                )
            except Exception as e:
                logger.warning(f"[approval] publish failed: {e}")

        try:
            result = await asyncio.wait_for(self._futures[request_id], timeout=req.timeout_sec)
            return bool(result[0]), str(result[1])
        except asyncio.TimeoutError:
            logger.info(f"[approval] timeout id={request_id} -> deny")
            return False, "timed out — auto-denied"
        finally:
            self._pending.pop(request_id, None)
            self._futures.pop(request_id, None)

    @staticmethod
    def summarize(req: ApprovalRequest) -> str:
        """Human-readable one-liner for the UI approval prompt and dashboard."""
        name = req.action_name
        if name in ("write_file", "edit_file"):
            return f"The agent wants to modify {req.arguments.get('path', '')}. Approve?"
        if name == "terminal":
            cmd = str(req.arguments.get("command", ""))[:80]
            return f"The agent wants to run: {cmd}. Approve?"
        return f"The agent wants to use {name}. Approve?"

    def approve(self, request_id: str, source: str = "ui") -> bool:
        fut = self._futures.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result((True, f"approved by {source}"))
        logger.info(f"[approval] {request_id} approved via {source}")
        self.note_activity()
        return True

    def deny(self, request_id: str, source: str = "ui") -> bool:
        fut = self._futures.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result((False, f"denied by {source}"))
        logger.info(f"[approval] {request_id} denied via {source}")
        self.note_activity()
        return True

    def pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())

    def newest_within_voice_window(self) -> ApprovalRequest | None:
        now = datetime.now(timezone.utc)
        fresh = [
            req for req_id, req in self._pending.items()
            if not self._futures[req_id].done() and req.age_sec(now) <= self.voice_window_sec
        ]
        if not fresh:
            return None
        fresh.sort(key=lambda r: r.created_at, reverse=True)
        return fresh[0]

    def try_resolve_by_voice(self, text: str) -> str | None:
        """Bare yes/no only — never embedded in a longer sentence."""
        if not text:
            return None
        cleaned = text.strip().lower().rstrip(".!?,")
        tokens = re.findall(r"[a-z']+", cleaned)
        joined = " ".join(tokens)

        matched: str | None = None
        if joined in _YES_WORDS or (tokens and tokens[0] in _YES_WORDS and len(tokens) <= 3):
            matched = "yes"
        elif joined in _NO_WORDS or (tokens and tokens[0] in _NO_WORDS and len(tokens) <= 3):
            matched = "no"
        if matched is None:
            return None

        target = self.newest_within_voice_window()
        if target is None:
            return None
        if matched == "yes":
            self.approve(target.request_id, source="voice")
            return "approved"
        self.deny(target.request_id, source="voice")
        return "denied"