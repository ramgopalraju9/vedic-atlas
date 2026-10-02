"""Unit tests for the task service + repository (Feature D).

Uses an in-memory SQLite DB; no server needed.

Run: pytest tests/test_task_service.py
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tpa.persistence.session import Base
from tpa.persistence.models import task  # noqa: F401 (register table)
from tpa.persistence.repositories.task_repository import SqliteTaskRepository
from service.tasks.task_service import TaskService


def _service() -> TaskService:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    return TaskService(SqliteTaskRepository(session_factory=factory))


def test_add_list_complete_delete():
    svc = _service()
    t = svc.add("buy milk", notes="2%")
    assert t.id is not None

    assert [x.title for x in svc.list()] == ["buy milk"]
    assert svc.complete(t.id) is True
    assert svc.list() == []  # completed task not in pending
    assert len(svc.list(include_done=True)) == 1

    assert svc.delete(t.id) is True
    assert svc.list(include_done=True) == []


def test_missing_ids_return_false():
    svc = _service()
    assert svc.complete(999) is False
    assert svc.delete(999) is False


def test_blank_title_rejected():
    svc = _service()
    try:
        svc.add("   ")
        assert False, "expected ValueError"
    except ValueError:
        pass