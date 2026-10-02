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
    from domain.ports.memory_repository_port import MemoryRepositoryPort
    from service.agent.supervisor import SupervisorAgent
    from service.approval.approval_broker import ApprovalBroker
    from service.lookup.lookup_service import LookupService
    from service.memory.knowledge_base import KnowledgeBase
    from service.sensing.event_bus import EventBus


def _require(request: Request, attr: str, label: str):
    obj = getattr(request.app.state, attr, None)
    if obj is None:
        raise HTTPException(status_code=503, detail=f"{label} not initialized")
    return obj


def get_supervisor(request: Request) -> "SupervisorAgent":
    return _require(request, "supervisor", "supervisor")


def get_event_bus(request: Request) -> "EventBus":
    return _require(request, "event_bus", "event bus")


def get_approval_broker(request: Request) -> "ApprovalBroker":
    return _require(request, "approval_broker", "approval broker")


def get_knowledge_base(request: Request) -> "KnowledgeBase":
    return _require(request, "knowledge", "knowledge base")


def get_memory(request: Request) -> "MemoryRepositoryPort":
    return _require(request, "memory", "memory repository")


def get_lookup_service(request: Request) -> "LookupService":
    return _require(request, "lookup_service", "lookup service")


def get_governance(request: Request) -> "GovernanceProvider | None":
    """Governance is optional — routes must handle `None` (disabled) themselves."""
    return getattr(request.app.state, "governance", None)


def get_semantic_recall(request: Request):
    """Optional — None when embeddings are unavailable; routes handle that."""
    return getattr(request.app.state, "semantic_recall", None)


def get_task_service(request: Request):
    return _require(request, "task_service", "task service")