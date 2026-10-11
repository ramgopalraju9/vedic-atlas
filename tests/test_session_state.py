"""Phase 1 (docs/10): manifest fields, session-state policy, repository and the ToolAgent shadow write."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.entities.session_context import SessionContext
from domain.policies.destructive_policy import is_destructive
from domain.policies.session_state_policy import active_line, state_after_call, usable_slots
from schemas.tool_manifest_schema import ToolManifestSchema
from service.agent.tool_execution import ExecutedCall, TurnOutcome
from service.session.session_state import SessionStateService
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.persistence.models import session_context  # noqa: F401 (register table)
from tpa.persistence.repositories.session_context_repository import SqliteSessionContextRepository
from tpa.persistence.session import Base

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
NOW = datetime(2026, 10, 7, 14, 30)


def _repo():
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return SqliteSessionContextRepository(
        session_factory=sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    )


def _call(tool, args, ok=True):
    return ExecutedCall(tool, args, ok, "obs", None, False, None if ok else "boom", 1)


# ---- manifest fields ---------------------------------------------------------

def test_shipped_manifests_declare_destructive_actions_and_result_kind():
    assert MANIFESTS["tasks"].destructive_when == (("action", ("delete",)),)
    assert MANIFESTS["remember"].destructive_when == (("action", ("forget",)),)
    assert MANIFESTS["web_search"].returns == "document" and MANIFESTS["web_search"].max_result_tokens == 320
    assert MANIFESTS["get_weather"].returns == "value" and not MANIFESTS["get_weather"].destructive


def test_is_destructive_is_per_argument_not_per_tool():
    tasks = MANIFESTS["tasks"]
    assert is_destructive(tasks, {"action": "delete", "title": "bank"})
    assert not is_destructive(tasks, {"action": "add", "title": "milk"})
    assert not is_destructive(tasks, {"action": "list"})
    assert is_destructive(MANIFESTS["remember"], {"action": "forget", "topic": "x"})
    assert not is_destructive(MANIFESTS["get_weather"], {"place": "Tokyo"})


def _schema(**extra):
    base = {"name": "t", "agent": "a", "description": "d", "params": {"action": {"type": "string", "enum": ["a", "b"]}}}
    return ToolManifestSchema(**{**base, **extra})


def test_manifest_schema_rejects_bad_destructive_when_and_multiple_prompt_examples():
    with pytest.raises(ValidationError):
        _schema(destructive_when={"nope": ["x"]})
    with pytest.raises(ValidationError):
        _schema(destructive_when={"action": ["zzz"]})          # not in the enum
    with pytest.raises(ValidationError):
        _schema(destructive_when={"action": []})
    ex = {"user": "u", "calls": [{"tool": "t", "args": {"action": "a"}}], "prompt_example": True}
    with pytest.raises(ValidationError):
        _schema(examples=[ex, ex])
    assert _schema(examples=[ex]).to_domain().examples[0].prompt_example is True
    assert _schema().to_domain().returns == "value"            # defaults are inert


# ---- policy ------------------------------------------------------------------

def test_state_after_call_keeps_validated_slots_and_sets_ttl():
    s = state_after_call(MANIFESTS["get_weather"], {"place": "Tokyo"}, session_id="s1", speaker_id="", now=NOW, ttl_sec=900)
    assert s.tool == "get_weather" and s.slots == {"place": "Tokyo"} and s.expires_at == NOW + timedelta(seconds=900)


def test_empty_slots_are_not_remembered_and_destructive_calls_write_nothing():
    assert usable_slots({"place": "", "x": None, "y": 0}) == {"y": 0}
    assert state_after_call(MANIFESTS["tasks"], {"action": "delete", "title": "b"}, session_id="s", speaker_id="", now=NOW) is None


def test_active_line_format_expiry_and_age():
    s = SessionContext("s", "", "get_weather", {"place": "Tokyo"}, NOW - timedelta(minutes=3), NOW + timedelta(minutes=12))
    assert active_line(s, NOW) == "ACTIVE: get_weather | place=Tokyo | 3 min ago"
    assert active_line(s, NOW + timedelta(minutes=13)) == ""    # expired -> omitted entirely
    assert active_line(None, NOW) == ""
    fresh = SessionContext("s", "", "tasks", {}, NOW, NOW + timedelta(minutes=15))
    assert active_line(fresh, NOW) == "ACTIVE: tasks | just now"


# ---- repository --------------------------------------------------------------

def test_repository_upserts_one_row_per_session_and_speaker():
    repo = _repo()
    mk = lambda tool, sid="s1", spk="": SessionContext(sid, spk, tool, {"k": tool}, NOW, NOW + timedelta(minutes=15))
    repo.upsert(mk("a")); repo.upsert(mk("b"))
    assert repo.get("s1", "", NOW).tool == "b"                   # replaced, not appended
    repo.upsert(mk("c", spk="alice"))
    assert repo.get("s1", "", NOW).tool == "b" and repo.get("s1", "alice", NOW).tool == "c"   # scoped by speaker
    assert repo.get("other", "", NOW) is None                    # scoped by session


def test_repository_treats_expired_rows_as_absent_and_deletes_them():
    repo = _repo()
    repo.upsert(SessionContext("s1", "", "a", {}, NOW, NOW + timedelta(seconds=10)))
    repo.upsert(SessionContext("s2", "", "b", {}, NOW, NOW + timedelta(seconds=10)))
    later = NOW + timedelta(seconds=11)
    assert repo.get("s1", "", later) is None
    assert repo.purge_expired(later) == 1                        # s1 was lazily deleted; only s2 remains
    repo.upsert(SessionContext("s3", "", "c", {"p": "x"}, NOW, NOW + timedelta(minutes=5)))
    repo.clear("s3", "")
    assert repo.get("s3", "", NOW) is None


# ---- service -----------------------------------------------------------------

def test_service_writes_last_successful_call_only():
    svc = SessionStateService(_repo(), MANIFESTS, ttl_sec=900, now=lambda: NOW)
    assert svc.record_success("s1", "", [_call("get_weather", {"place": "Tokyo"}, ok=False)]) is None
    assert svc.get("s1") is None                                 # failure never writes
    svc.record_success("s1", "", [_call("get_weather", {"place": "Tokyo"}), _call("convert_currency", {"amount": 5}, ok=False)])
    assert svc.get("s1").tool == "get_weather"
    assert svc.active_line("s1") == "ACTIVE: get_weather | place=Tokyo | just now"


def test_service_never_remembers_a_destructive_call_or_falls_back_past_it():
    svc = SessionStateService(_repo(), MANIFESTS, now=lambda: NOW)
    svc.record_success("s1", "", [_call("get_weather", {"place": "Tokyo"}), _call("tasks", {"action": "delete", "title": "x"})])
    assert svc.get("s1") is None


def test_service_swallows_repository_errors():
    class Broken:
        def get(self, *a): raise RuntimeError("db down")
        def upsert(self, *a): raise RuntimeError("db down")
        def purge_expired(self, *a): raise RuntimeError("db down")
    svc = SessionStateService(Broken(), MANIFESTS, now=lambda: NOW)
    assert svc.get("s") is None and svc.active_line("s") == "" and svc.purge_expired() == 0
    assert svc.record_success("s", "", [_call("get_weather", {"place": "Tokyo"})]) is None
