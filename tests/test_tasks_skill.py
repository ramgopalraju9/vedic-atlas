"""Tests for task matching, TasksSkill, and completed-task purge.

In-memory SQLite; no server needed. Run: pytest tests/test_tasks_skill.py
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.policies.task_matching import best_matches
from service.skills.builtin.tasks import TasksSkill
from service.tasks.task_service import TaskService
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.persistence.models import task  # noqa: F401 (register table)
from tpa.persistence.repositories.task_repository import SqliteTaskRepository
from tpa.persistence.session import Base


_MANIFEST = next(m for m in YamlToolManifestStore().load_all() if m.name == "tasks")


def _service() -> TaskService:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    return TaskService(SqliteTaskRepository(session_factory=factory))


def _run(skill, **params):
    return asyncio.run(skill.execute(AgentContext(user_message="x"), **params))


def test_matching_milk_packets_finds_get_milk_home():
    assert best_matches("milk packets", {1: "bring vegies", 2: "get milk home"}) == [2]


def test_matching_none_and_ambiguous():
    titles = {1: "call bank", 2: "call mom"}
    assert best_matches("dentist", titles) == []
    assert sorted(best_matches("call", titles)) == [1, 2]
    assert best_matches("call mom", titles) == [2]


def test_add_then_complete_by_phrase_updates_db():
    svc = _service()
    skill = TasksSkill(svc, _MANIFEST)
    assert _run(skill, action="add", title="get milk home").success
    assert _run(skill, action="add", title="bring vegies").success

    res = _run(skill, action="complete", title="milk packets")
    assert res.success and "get milk home" in res.output
    assert [t.title for t in svc.list()] == ["bring vegies"]  # DB really changed
    done = svc.list(include_done=True)
    assert any(t.done and t.completed_at is not None for t in done)


def test_complete_no_match_changes_nothing():
    svc = _service()
    skill = TasksSkill(svc, _MANIFEST)
    _run(skill, action="add", title="bring vegies")
    res = _run(skill, action="complete", title="dentist")
    assert not res.success and "Nothing was changed" in res.error
    assert len(svc.list()) == 1


def test_complete_ambiguous_asks_user():
    svc = _service()
    skill = TasksSkill(svc, _MANIFEST)
    _run(skill, action="add", title="call bank")
    _run(skill, action="add", title="call mom")
    res = _run(skill, action="complete", title="call")
    assert not res.success and "Ask the user which one" in res.error
    assert len(svc.list()) == 2


def test_duplicate_add_is_not_created():
    svc = _service()
    skill = TasksSkill(svc, _MANIFEST)
    _run(skill, action="add", title="bring vegies")
    res = _run(skill, action="add", title="Bring the vegies")
    assert res.success and "Nothing added" in res.output
    assert len(svc.list()) == 1


def test_list_shows_ids_and_empty_state():
    svc = _service()
    skill = TasksSkill(svc, _MANIFEST)
    assert "empty" in _run(skill, action="list").output
    _run(skill, action="add", title="code")
    out = _run(skill, action="list").output
    assert "#1" in out and "code" in out


def test_validation_rejects_bad_input():
    skill = TasksSkill(_service(), _MANIFEST)
    assert not _run(skill, action="explode").success
    assert not _run(skill, action="add").success
    assert not _run(skill, action="add", title="x", shell="rm -rf").success
    assert not _run(skill, action="add", title="x" * 500).success


def test_delete_by_id_and_purge_old_completed():
    svc = _service()
    a, b = svc.add("old done"), svc.add("fresh done")
    svc.complete(a.id)
    svc.complete(b.id)
    assert svc.purge_completed(now=datetime.now()) == 0  # both just completed
    future = datetime.now() + timedelta(days=8)
    assert svc.purge_completed(now=future) == 2
    assert svc.list(include_done=True) == []
