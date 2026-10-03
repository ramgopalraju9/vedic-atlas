"""Phase 4: grounding policy, trace persistence, trace recording by ToolAgent, and the API route."""

import asyncio
import json
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.entities.turn_trace import TurnTrace
from domain.policies.grounding_policy import is_grounded, ungrounded_numbers
from tpa.persistence.models import turn_trace  # noqa: F401 (register table)
from tpa.persistence.repositories.trace_repository import SqliteTraceRepository
from tpa.persistence.session import Base


def _repo(url="sqlite:///:memory:"):
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    return SqliteTraceRepository(session_factory=factory)


# ---- grounding ---------------------------------------------------------------

def test_numbers_must_come_from_the_tool_result():
    result = "WEATHER for Mumbai: clear sky, 31.0 C (feels like 32.2 C), humidity 41%, wind 8.3 km/h"
    assert is_grounded("It's 31 degrees in Mumbai with 41 percent humidity.", [result])
    assert not is_grounded("It's 35 degrees in Mumbai.", [result])
    assert ungrounded_numbers("It's 35 degrees and 41 percent", [result]) == [35.0]


def test_rounding_and_thousands_separators_are_tolerated():
    result = "250 USD = 24,081.00 INR (1 USD = 96.324 INR; reference rate of 2026-10-02)"
    assert is_grounded("250 dollars is about 24,081 rupees at 96.32.", [result])
    assert not is_grounded("250 dollars is about 25,000 rupees.", [result])


def test_user_numbers_and_dates_count_as_sources():
    assert is_grounded("Over the last 7 days there were three launches.", ["launches", "news from the last 7 days"])
    assert is_grounded("Today is the 3rd.", ["nothing numeric", "Saturday 03 October 2026 20:30"])


def test_urls_are_never_grounded():
    assert not is_grounded("See https://example.com for more.", ["ISRO plans a launch"])
    assert is_grounded("ISRO plans a launch soon.", ["ISRO plans a launch"])


# ---- trace repository --------------------------------------------------------

def _trace(msg="what are my tasks?", agent="tasks", when=None):
    return TurnTrace(
        request_id="abc123", agent=agent, user_message=msg, reply="You have 1 task.", decided="tool",
        created_at=when or datetime.now(), total_ms=3200,
        calls=[{"tool": "tasks", "args": {"action": "list"}, "ok": True, "ms": 3, "result": "1 task(s)", "error": None}],
        prompt_tokens={"call": 374}, timings_ms={"decide": 3100, "execute": 3}, notes=["n1"],
    )


def test_trace_round_trips_with_calls_tokens_and_notes():
    repo = _repo()
    tid = repo.record(_trace())
    got = repo.recent(5)[0]
    assert got.id == tid and got.agent == "tasks" and got.decided == "tool"
    assert got.calls[0]["tool"] == "tasks" and got.calls[0]["ok"] is True
    assert got.prompt_tokens == {"call": 374} and got.timings_ms["decide"] == 3100 and got.notes == ["n1"]


def test_recent_is_newest_first_and_limited():
    repo = _repo()
    now = datetime.now()
    for i in range(5):
        repo.record(_trace(msg=f"m{i}", when=now + timedelta(seconds=i)))
    assert [t.user_message for t in repo.recent(3)] == ["m4", "m3", "m2"]


def test_purge_removes_only_old_traces():
    repo = _repo()
    repo.record(_trace(msg="old", when=datetime.now() - timedelta(days=30)))
    repo.record(_trace(msg="new"))
    assert repo.purge_before(datetime.now() - timedelta(days=14)) == 1
    assert [t.user_message for t in repo.recent(10)] == ["new"]


# ---- ToolAgent writes a trace per turn -----------------------------------------

def test_tool_agent_records_a_trace_for_tool_and_no_tool_turns():
    from tests.test_tool_turn import _calls, _env  # reuse the scripted-model harness

    agent, client, service, convo, runner = _env([_calls(("tasks", {"action": "list"})), _calls()], chat_reply="Doing well!")
    repo = _repo()
    agent._traces = repo
    service.add("bring vegies")

    asyncio.run(agent.execute(AgentContext(user_message="what are my tasks?")))
    asyncio.run(agent.execute(AgentContext(user_message="how are you?")))

    newest, older = repo.recent(2)
    assert (older.decided, older.calls[0]["tool"], older.calls[0]["ok"]) == ("tool", "tasks", True)
    assert "bring vegies" in older.reply and older.total_ms >= 0 and "call" in older.prompt_tokens
    assert (newest.decided, newest.calls, newest.reply) == ("no-tool", [], "Doing well!")


def test_a_failing_trace_store_never_breaks_the_turn():
    from tests.test_tool_turn import _calls, _env

    class _Broken:
        def record(self, trace):
            raise RuntimeError("db locked")

    agent, *_ = _env([_calls(("tasks", {"action": "list"}))])
    agent._traces = _Broken()
    res = asyncio.run(agent.execute(AgentContext(user_message="what are my tasks?")))
    assert "empty" in res.response.lower()


# ---- API route ------------------------------------------------------------------

def test_trace_route_returns_recent_traces(tmp_path):
    from controller.routes import trace as trace_route

    repo = _repo(f"sqlite:///{(tmp_path / 't.db').as_posix()}")  # file DB: the test client runs in another thread
    repo.record(_trace())
    app = FastAPI()
    app.state.trace_repo = repo
    app.include_router(trace_route.router, prefix="/api")
    body = TestClient(app).get("/api/trace?limit=5").json()
    assert body["traces"][0]["agent"] == "tasks" and body["traces"][0]["calls"][0]["tool"] == "tasks"
    json.dumps(body)  # fully serialisable
