"""Tasks API - local to-do management (Feature D, Productivity focus area)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from controller.dependencies.providers import get_task_service
from schemas.tasks import TaskCreate, TaskList, TaskOut
from service.tasks.task_service import TaskService

router = APIRouter()


def _out(t) -> TaskOut:
    return TaskOut(id=t.id, title=t.title, done=t.done, notes=t.notes, due_at=t.due_at, created_at=t.created_at)


@router.get("/tasks", response_model=TaskList)
async def list_tasks(include_done: bool = False, service: TaskService = Depends(get_task_service)):
    return TaskList(tasks=[_out(t) for t in service.list(include_done=include_done)])


@router.post("/tasks", response_model=TaskOut)
async def add_task(body: TaskCreate, service: TaskService = Depends(get_task_service)):
    return _out(service.add(body.title, notes=body.notes, due_at=body.due_at))


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: int, service: TaskService = Depends(get_task_service)) -> dict:
    if not service.complete(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "completed", "id": task_id}


@router.post("/tasks/{task_id}/uncomplete")
@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, service: TaskService = Depends(get_task_service)) -> dict:
    if not service.delete(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "deleted", "id": task_id}