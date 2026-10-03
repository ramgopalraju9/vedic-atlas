"""Trace API — what the tool harness actually did on recent turns.

Answers "did the call really happen?": for each tool turn, what the user said,
every tool call with its real result, the guard decisions, stage timings and
prompt sizes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from controller.dependencies.providers import get_trace_repo
from domain.ports.trace_repository_port import TraceRepositoryPort
from schemas.trace import TraceList, TraceOut

router = APIRouter()


@router.get("/trace", response_model=TraceList)
async def recent_traces(
    limit: int = Query(default=20, ge=1, le=200),
    repo: TraceRepositoryPort = Depends(get_trace_repo),
) -> TraceList:
    return TraceList(traces=[TraceOut(**t.__dict__) for t in repo.recent(limit)])
