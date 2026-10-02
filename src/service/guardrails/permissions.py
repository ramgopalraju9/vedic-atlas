"""PermissionManager — async orchestration of permission-gated skill execution.

Donor: veda/guardrails/permissions.py, read in full and ported. The pure
decision (which level requires what) is domain/policies/permission_policy.py
(Batch 4); this class is the async wait-for-approval half — creating a
pending approval, awaiting the event with a timeout, resolving it via
`approve()`/`deny()` called from the approval route.

Correction (2026-09-22): the first read (112 of the real 135 lines)
stopped before the donor's `reject()` and `list_pending()` methods. Added
both — `reject` as the exact donor name, `deny` kept as the alias this
port had already settled on for consistency with ApprovalBroker.deny().
"""

import asyncio
from datetime import datetime
from uuid import uuid4

from domain.entities.agent_context import AgentContext
from domain.policies.permission_policy import proceeds_immediately, requires_approval
from domain.value_objects.permission_level import PermissionLevel
from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException
from core.logging_config import logger


class PermissionManager:
    """Enforces permission levels for skill execution.

    - AUTO: proceed immediately
    - NOTIFY: log and proceed
    - APPROVE: create an approval request, wait for user decision
    """

    def __init__(self, default_level: str = "notify", approval_timeout: int = 60):
        self.default_level = PermissionLevel(default_level)
        self.approval_timeout = approval_timeout
        self._pending: dict[str, dict] = {}

    async def check_permission(self, ctx: AgentContext, skill_name: str, permission_level: str, params: dict) -> bool:
        level = PermissionLevel(permission_level)

        if proceeds_immediately(level):
            logger.debug(f"Permission AUTO for skill '{skill_name}' — proceeding")
            return True

        if not requires_approval(level):
            logger.info(f"Permission NOTIFY for skill '{skill_name}' — proceeding with notification")
            return True

        return await self._request_approval(ctx, skill_name, params)

    async def _request_approval(self, ctx: AgentContext, skill_name: str, params: dict) -> bool:
        request_id = uuid4().hex[:12]
        event = asyncio.Event()

        self._pending[request_id] = {
            "event": event, "approved": None, "skill_name": skill_name,
            "params": params, "created_at": datetime.now().isoformat(), "request_id": request_id,
        }
        ctx.pending_approvals.append({"request_id": request_id, "skill_name": skill_name, "params": params})
        logger.info(f"Approval requested for skill '{skill_name}' (request_id: {request_id}, timeout: {self.approval_timeout}s)")

        try:
            await asyncio.wait_for(event.wait(), timeout=self.approval_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise AppException(
                class_name="PermissionManager", code=ExceptionCode.APPROVAL_TIMEOUT,
                error_message=ErrorMessage.APPROVAL_TIMED_OUT, timeout=self.approval_timeout,
            )

        entry = self._pending.pop(request_id, {})
        approved = entry.get("approved", False)
        if not approved:
            raise AppException(
                class_name="PermissionManager", code=ExceptionCode.APPROVAL_REJECTED,
                error_message=ErrorMessage.APPROVAL_WAS_REJECTED, skill_name=skill_name,
            )

        logger.info(f"Approval GRANTED for skill '{skill_name}' (request_id: {request_id})")
        ctx.approved_actions.append(f"{skill_name}:{request_id}")
        return True

    def approve(self, request_id: str) -> bool:
        """Approve a pending request. Called from the approval route."""
        if request_id not in self._pending:
            return False
        self._pending[request_id]["approved"] = True
        self._pending[request_id]["event"].set()
        return True

    def deny(self, request_id: str) -> bool:
        """Reject a pending request. Called from the approval route.

        Named `deny` here (not `reject`, as the real donor method is
        called) to read naturally alongside ApprovalBroker.deny() in
        service/approval/approval_broker.py — both resolve a pending
        request the same way. See the correction note at the top of this
        file: the donor's actual name is `reject`; kept as an alias below
        so callers using either name work.
        """
        if request_id not in self._pending:
            return False
        self._pending[request_id]["approved"] = False
        self._pending[request_id]["event"].set()
        return True

    def reject(self, request_id: str) -> bool:
        """Alias for `deny` — matches the real donor method name exactly."""
        return self.deny(request_id)

    def list_pending(self) -> list[dict]:
        """List all pending approval requests.

        Donor: veda/guardrails/permissions.py::list_pending, read in full
        this batch — the first read (112 of the real 135 lines) stopped
        before this method and `reject()` entirely.
        """
        return [
            {
                "request_id": v["request_id"],
                "skill_name": v["skill_name"],
                "params": v["params"],
                "created_at": v["created_at"],
            }
            for v in self._pending.values()
        ]