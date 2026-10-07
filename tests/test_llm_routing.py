"""Hybrid routing: rules first, then a constrained LLM call for what they did not recognise.

Real manifests, prompts and Supervisor; the model is scripted. Run: pytest tests/test_llm_routing.py
"""

import asyncio
import json

import pytest

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.policies.routing_policy import match_agent, pick_agent
from domain.entities.agent_profile import AgentProfile
from service.agent.base_agent import BaseAgent
from service.agent.llm_router import LlmRouter
from service.agent.registry import AgentRegistry
from service.agent.supervisor import SupervisorAgent
from service.prompting.prompt_composer import PromptComposer
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

MANIFESTS = YamlToolManifestStore().load_all()
DESCRIPTIONS = {
    "responder": "General conversation, Q&A, brainstorming",
    "system": "Device actions: open/close apps, volume, running processes",
    "tasks": "The user's to-do list - add, list, complete or delete tasks.",
    "lookup": "Current weather for a place; Convert an amount between two currencies; Search the web for current facts.",
}


class _Agent(BaseAgent):
    def __init__(self, name, triggers=()):
        super().__init__(name=name, description=DESCRIPTIONS[name], triggers=list(triggers))

    async def execute(self, ctx):
        return AgentResult(agent_name=self.name, response=self.name)

    async def execute_stream(self, ctx, cancel_event=None):
        yield self.name


class _Client:
    """Scripted model that records the schema and prompt it was given."""

    def __init__(self, answer=None, error=None):
        self._answer, self._error = answer, error
        self.calls = []

    async def complete(self, prompt, system="", model=None, timeout=None, *, num_predict=None, json_schema=None, temperature=None):
        self.calls.append({"prompt": prompt, "system": system, "schema": json_schema, "temperature": temperature, "n": num_predict})
        if self._error:
            raise self._error
        return self._answer


def _supervisor(client, llm=True):
    triggers = {}
    for m in MANIFESTS:
        triggers.setdefault(m.agent, []).extend(m.triggers)
    registry = AgentRegistry()
    for name in ("responder", "system", "tasks", "lookup"):
        registry.register(_Agent(name, triggers.get(name, ())))
    composer = PromptComposer(FilePromptStore(), MANIFESTS)
    router = LlmRouter(client, composer) if llm else None
    sup = SupervisorAgent(agent_registry=registry, client=client, default_agent="responder", llm_router=router)
    registry.register(sup)
    return sup


def _route(sup, text):
    ctx = AgentContext(user_message=text)
    agent = asyncio.run(sup._pick(ctx))
    return agent.name, ctx.metadata.get("routed_via")


# ---- the policy distinguishes "no match" from "default" ---------------------------------

def test_match_agent_reports_a_miss_as_none_while_pick_agent_still_defaults():
    profiles = (
        AgentProfile(name="responder", description="General conversation", model_alias=""),
        AgentProfile(name="tasks", description="to-do list", model_alias="", triggers=(r"\btasks?\b",)),
    )
    assert match_agent("what are my tasks", profiles) == "tasks"
    assert match_agent("tell me a joke", profiles) is None
    assert pick_agent("tell me a joke", profiles, "responder") == "responder"


# ---- rules first: the LLM is never called for what the rules already decide ---------------

@pytest.mark.parametrize("text,agent", [
    ("what's the weather in Mumbai", "lookup"), ("what are my tasks", "tasks"), ("open notepad", "system"),
    ("how much is 100 dollars in rupees", "lookup"), ("latest news on ISRO", "lookup"),
])
def test_recognised_messages_are_routed_by_rules_without_a_model_call(text, agent):
    client = _Client(answer=json.dumps({"agent": "responder"}))
    assert _route(_supervisor(client), text) == (agent, "rules")
    assert client.calls == []


# ---- the LLM decides the rest -------------------------------------------------------------

def test_an_unrecognised_message_is_routed_by_the_constrained_llm_call():
    client = _Client(answer=json.dumps({"agent": "lookup"}))
    agent, via = _route(_supervisor(client), "do I need a jacket in Pune tonight")
    assert agent == "lookup" and via.startswith("llm")
    call = client.calls[0]
    # chat is listed last: a small model leans toward the first option it sees
    assert call["schema"]["properties"]["agent"]["enum"] == ["system", "tasks", "lookup", "responder"]
    assert call["temperature"] == 0.0 and call["n"] <= 64
    assert "Message: do I need a jacket in Pune tonight" in call["prompt"]


def test_the_routing_prompt_names_every_agent_and_tool_and_carries_worked_examples():
    client = _Client(answer=json.dumps({"agent": "responder"}))
    _route(_supervisor(client), "a message nothing matches xyzzy")
    system = client.calls[0]["system"]
    assert "- lookup: " in system and "- system: " in system
    for m in MANIFESTS:                                   # the router is told which tools each specialist owns
        assert m.name in system
    assert 'Message: who is the CEO of Apple\nAnswer: {"agent": "lookup"}' in system
    assert 'Message: I need to book a dentist visit\nAnswer: {"agent": "tasks"}' in system
    from domain.policies.token_budget_policy import PromptBudgets, estimate_tokens
    assert estimate_tokens(system) < PromptBudgets().route


def test_llm_says_chat_means_default_agent():
    client = _Client(answer=json.dumps({"agent": "responder"}))
    assert _route(_supervisor(client), "I feel like a walk")[0] == "responder"


def test_model_failure_falls_back_to_the_default_agent():
    for client in (_Client(error=RuntimeError("model gone")), _Client(answer="not json"), _Client(answer=json.dumps({"agent": "nope"}))):
        assert _route(_supervisor(client), "something ambiguous xyzzy") == ("responder", "default")


def test_llm_routing_off_means_rules_only_like_before():
    client = _Client(answer=json.dumps({"agent": "lookup"}))
    assert _route(_supervisor(client, llm=False), "do I need a jacket in Pune tonight") == ("responder", "default")
    assert client.calls == []


def test_recent_turns_are_given_to_the_router_for_follow_ups():
    from datetime import datetime

    from domain.entities.conversation import Turn

    turns = [Turn(id=None, session_id="s", role="user", content="what's the weather in Pune", created_at=datetime.now()),
             Turn(id=None, session_id="s", role="assistant", content="It is 26 degrees.", created_at=datetime.now())]
    client = _Client(answer=json.dumps({"agent": "lookup"}))
    router = LlmRouter(client, PromptComposer(FilePromptStore(), MANIFESTS), history=lambda: turns)
    decision = asyncio.run(router.route("and in Mumbai?", [(n, d) for n, d in DESCRIPTIONS.items()]))
    assert decision.agent == "lookup"
    assert "RECENT:" in client.calls[0]["prompt"] and "weather in Pune" in client.calls[0]["prompt"]
