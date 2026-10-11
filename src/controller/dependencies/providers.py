"""FastAPI dependency providers — typed `Depends()` accessors over `app.state`.

★ new. Donor routes each hand-rolled their own `_broker(request)` /
`_supervisor(request)` helper (approval.py, config.py) with the same
"getattr + 503 if missing" shape; persona.py and chat.py instead read
`app.state.X` unguarded. This module gives every route in this batch one
consistent, typed way to reach the services server.py (Batch 11) will wire
onto `app.state` at startup, so a service that failed to initialize fails
loudly with a 503 rather than an unhandled AttributeError deep in a route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from domain.ports.governance_port import GovernanceProvider
    from service.agent.supervisor import SupervisorAgent
    from service.sensing.ambient_dispatcher import AmbientDispatcher
    from service.approval.approval_broker import ApprovalBroker
    from domain.ports.trace_repository_port import TraceRepositoryPort
    from service.lookup.health_service import LookupHealthService
    from service.memory.knowledge_base import KnowledgeBase
    from service.speakers.speaker_enrollment_service import SpeakerEnrollmentService


def _require(request: Request, attr: str, label: str):
    obj = getattr(request.app.state, attr, None)
    if obj is None:
        raise HTTPException(status_code=503, detail=f"{label} not initialized")
    return obj


def get_supervisor(request: Request) -> "SupervisorAgent":
    return _require(request, "supervisor", "supervisor")


def get_ambient_dispatcher(request: Request) -> "AmbientDispatcher":
    return _require(request, "ambient_dispatcher", "ambient dispatcher")


def get_approval_broker(request: Request) -> "ApprovalBroker":
    return _require(request, "approval_broker", "approval broker")


def get_knowledge_base(request: Request) -> "KnowledgeBase":
    return _require(request, "knowledge", "knowledge base")


def get_trace_repo(request: Request) -> "TraceRepositoryPort":
    return _require(request, "trace_repo", "trace repository")


def get_lookup_health(request: Request) -> "LookupHealthService":
    return _require(request, "lookup_health", "lookup health service")


def get_governance(request: Request) -> "GovernanceProvider | None":
    """Governance is optional — routes must handle `None` (disabled) themselves."""
    return getattr(request.app.state, "governance", None)


def get_task_service(request: Request):
    return _require(request, "task_service", "task service")


def get_google_auth(request: Request):
    """503 when Google tools are not registered (online tools disabled)."""
    return _require(request, "google_auth", "Google sign-in")


def get_reminders(request: Request):
    return _require(request, "reminders", "reminders")


def get_speaker_enrollment_service(request: Request) -> "SpeakerEnrollmentService":
    """503 when speaker ID is disabled/unavailable - same "not initialized"
    semantics as every other _require'd service, just conditionally built."""
    return _require(request, "speaker_enrollment_service", "speaker enrollment service")