"""Phase 2: the staged ToolTurnRunner and ToolAgent (legacy path, deleted at cutover).

Real manifests, prompts, TasksSkill, SkillRunner and an in-memory DB; only the
model is scripted. Run: pytest tests/test_tool_turn.py
"""

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest, ToolParam
from service.agent.base_agent import BaseAgent
from service.agent.tool_agent import ToolAgent
from service.agent.tool_turn_runner import ToolTurnRunner
from service.agent.tool_use_guard import ToolUseGuard
from service.prompting.prompt_composer import PromptComposer
from service.skills.base_skill import BaseSkill
from service.skills.builtin.tasks import TasksSkill
from service.skills.registry import SkillRegistry
from service.skills.skill_runner import SkillRunner
from service.tasks.task_service import TaskService
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.persistence.models import task  # noqa: F401 (register table)
from tpa.persistence.repositories.task_repository import SqliteTaskRepository
from tpa.persistence.session import Base

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}


def _calls(*calls):
    return json.dumps({"calls": [{"tool": t, "args": a} for t, a in calls]})


class _Client:
    """Scripted model. Records every call (prompt, schema, temperature)."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    async def complete(self, prompt, system="", model=None, timeout=None, *, num_predict=None,
                       json_schema=None, temperature=None):
        self.calls.append({"prompt": prompt, "system": system, "schema": json_schema, "temperature": temperature})
        return self._scripted.pop(0)


class _Chat(BaseAgent):
    def __init__(self, reply):
        super().__init__(name="responder", description="chat")
        self._reply = reply

    async def execute(self, ctx):
        return AgentResult(agent_name="responder", response=self._reply)

    async def execute_stream(self, ctx, cancel_event=None):
        yield self._reply


class _Convo:
    def __init__(self):
        self.turns = []
        self.added = []

    def add_turn(self, role, content, session_id=None):
        self.added.append((role, content))


def _env(scripted, chat_reply="Doing well!", extra_skills=(), extra_manifests=()):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    service = TaskService(SqliteTaskRepository(session_factory=factory))
    registry = SkillRegistry()
    registry.register(TasksSkill(service, MANIFESTS["tasks"]))
    for sk in extra_skills:
        registry.register(sk)
    manifests = dict(MANIFESTS)
    manifests.update({m.name: m for m in extra_manifests})
    guard = ToolUseGuard(manifests.values())
    composer = PromptComposer(FilePromptStore(), list(manifests.values()))
    client = _Client(scripted)
    convo = _Convo()
    runner = ToolTurnRunner(
        client=client, composer=composer, skill_runner=SkillRunner(registry), manifests=manifests,
        guard=guard, conversation=convo,
    )
    agent = ToolAgent(
        name="tasks", description="tasks", tool_names=["tasks"], triggers=list(MANIFESTS["tasks"].triggers),
        runner=runner, fallback=_Chat(chat_reply), guard=guard, conversation=convo,
    )
    return agent, client, service, convo, runner


def _run(agent, text):
    return asyncio.run(agent.execute(AgentContext(user_message=text)))


# ---- the staged turn -----------------------------------------------------

def test_complete_by_phrase_uses_one_constrained_call_and_a_templated_reply():
    agent, client, service, convo, _ = _env([_calls(("tasks", {"action": "complete", "title": "milk packets"}))])
    service.add("get milk home")
    res = _run(agent, "yeah I bought the milk packets")
    assert res.response == "Marked get milk home as done."
    assert service.list() == []                                  # DB really changed
    assert len(client.calls) == 1                                # template reply: no narrate call
    assert client.calls[0]["schema"] is not None                 # decoding was constrained
    assert client.calls[0]["temperature"] == 0.1
    assert ("user", "yeah I bought the milk packets") in convo.added


def test_schema_only_allows_the_agents_tools():
    agent, client, *_ = _env([_calls(("tasks", {"action": "list"}))])
    _run(agent, "what are my tasks?")
    item = client.calls[0]["schema"]["properties"]["calls"]["items"]
    assert item["properties"]["tool"] == {"const": "tasks"}


def test_an_empty_decision_is_never_forced():
    """The model reading the whole sentence decided no tool; no keyword may override it (Bug B)."""
    agent, client, service, *_ = _env([_calls()], chat_reply="Sure, here is the chat answer.")
    service.add("bring vegies")
    res = _run(agent, "what are my tasks?")
    assert res.response == "Sure, here is the chat answer."
    assert len(client.calls) == 1                                              # one decide call, no retry
    assert client.calls[0]["schema"]["properties"]["calls"]["minItems"] == 0
    assert "must call a tool" not in client.calls[0]["prompt"]


def test_unparseable_output_is_treated_as_no_call_not_retried():
    agent, client, *_ = _env(["not json at all"], chat_reply="Chat answer.")
    assert _run(agent, "show my to-do list").response == "Chat answer."
    assert len(client.calls) == 1


def test_not_required_and_empty_hands_over_to_chat():
    agent, client, *_ = _env([_calls()], chat_reply="Doing well, thanks!")
    assert _run(agent, "how are you?").response == "Doing well, thanks!"
    assert len(client.calls) == 1


def test_chat_fallback_cannot_claim_a_task_action():
    agent, *_ = _env([_calls()], chat_reply="Task added: bring vegies.")
    res = _run(agent, "how are you?")
    assert "can't confirm" in res.response


def test_two_actions_in_one_message():
    agent, client, service, *_ = _env([_calls(
        ("tasks", {"action": "complete", "title": "milk"}),
        ("tasks", {"action": "add", "title": "code"}),
    )])
    service.add("get milk home")
    res = _run(agent, "I bought the milk and I need to code")
    assert "Marked get milk home as done." in res.response and "Added code to your list." in res.response
    assert [t.title for t in service.list()] == ["code"]


def test_no_match_changes_nothing_and_says_so():
    agent, client, service, *_ = _env([_calls(("tasks", {"action": "complete", "title": "dentist"}))])
    service.add("bring vegies")
    res = _run(agent, "I finished the dentist thing")
    assert "couldn't find a task like dentist" in res.response
    assert len(service.list()) == 1


def test_calls_to_tools_outside_the_agent_are_dropped():
    agent, client, service, *_ = _env([_calls(("terminal", {"command": "rm -rf /"})), _calls()], chat_reply="ok")
    res = _run(agent, "how are you?")
    assert res.response == "ok"  # the foreign tool call was discarded, nothing ran


def test_inference_failure_degrades_to_chat_not_a_crash():
    class _Boom(_Client):
        async def complete(self, *a, **k):
            raise RuntimeError("model gone")

    agent, client, service, convo, runner = _env([], chat_reply="fallback chat")
    runner._client = _Boom([])
    assert _run(agent, "how are you?").response == "fallback chat"


# ---- LLM-narrated tools (search-style) ----------------------------------

class _EchoSkill(BaseSkill):
    final = False

    def __init__(self):
        super().__init__(name="web_search", description="search", permission_level="auto")

    def get_parameters_description(self):
        return "query"

    async def execute(self, ctx, **params):
        return SkillResult(skill_name=self.name, success=True, output="Result: Oct 3 2026, India won by 5 wickets.",
                           metadata={"spoken": "India won by 5 wickets.", "final": self.final})


_SEARCH = ToolManifest(
    name="web_search", agent="tasks", description="Search the web.", reply_mode="llm",
    params=(ToolParam("query", "string", required=True),),
)


def test_llm_tool_narrates_over_the_tool_result_only():
    agent, client, *_ = _env(
        [_calls(("web_search", {"query": "who won"})), "India won by 5 wickets."],
        extra_skills=[_EchoSkill()], extra_manifests=[_SEARCH],
    )
    agent.skills.append("web_search")
    res = _run(agent, "who won the match")
    assert res.response == "India won by 5 wickets."
    narrate = client.calls[1]
    assert "TOOL RESULT:" in narrate["prompt"] and "5 wickets" in narrate["prompt"]
    assert narrate["schema"] is None  # narration is free text


def test_ungrounded_narration_falls_back_to_the_tool_spoken_text():
    agent, client, service, convo, runner = _env(
        [_calls(("web_search", {"query": "who won"})), "India won by 9 wickets.", "India won by 9 wickets."],
        extra_skills=[_EchoSkill()], extra_manifests=[_SEARCH],
    )
    runner._grounding_check = lambda reply, results, user: "9" not in reply
    agent.skills.append("web_search")
    res = _run(agent, "who won the match")
    assert res.response == "India won by 5 wickets."


def test_final_observation_skips_the_narrate_stage():
    skill = _EchoSkill()
    skill.final = True
    agent, client, *_ = _env([_calls(("web_search", {"query": "who won"}))], extra_skills=[skill], extra_manifests=[_SEARCH])
    agent.skills.append("web_search")
    res = _run(agent, "who won the match")
    assert res.response == "India won by 5 wickets."
    assert len(client.calls) == 1  # only the decide call; no second inference


def test_narrate_prompt_carries_todays_date():
    agent, client, *_ = _env(
        [_calls(("web_search", {"query": "who won"})), "India won by 5 wickets."],
        extra_skills=[_EchoSkill()], extra_manifests=[_SEARCH],
    )
    agent.skills.append("web_search")
    _run(agent, "who won the match")
    assert "TODAY:" in client.calls[1]["prompt"]
