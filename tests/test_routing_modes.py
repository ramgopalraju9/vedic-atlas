"""routing.mode: keyword | hybrid | model. A scripted router stands in for the model; everything else is real."""

import asyncio
from pathlib import Path

import pytest
import yaml

from domain.entities.agent_context import AgentContext
from domain.entities.agent_profile import AgentProfile
from domain.entities.agent_result import AgentResult
from domain.entities.route_decision import RouteDecision
from domain.policies.routing_policy import is_confident, match_agent_detail
from schemas.config_schemas import RoutingConfig
from service.agent.base_agent import BaseAgent
from service.agent.registry import AgentRegistry
from service.agent.supervisor import SupervisorAgent
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

MANIFESTS = YamlToolManifestStore().load_all()
DESCRIPTIONS = {
    "responder": "General conversation, Q&A, brainstorming",
    "system": "Device actions: open/close apps, volume, running processes",
    "tasks": "The user's to-do list - add, list, complete or delete tasks.",
    "lookup": "Live data from the web: weather, currency rates, news headlines, prices and scores",
}


class _Agent(BaseAgent):
    def __init__(self, name, triggers=()):
        super().__init__(name=name, description=DESCRIPTIONS[name], triggers=list(triggers))

    async def execute(self, ctx):
        return AgentResult(agent_name=self.name, response=self.name)

    async def execute_stream(self, ctx, cancel_event=None):
        yield self.name


class _Router:
    """RouterPort double: answers with `agent` (or None = could not decide) and counts calls."""

    def __init__(self, agent=None):
        self.agent, self.calls, self.warmups, self.paused = agent, [], 0, 0.0

    async def route(self, message, agents):
        self.calls.append(message)
        return None if self.agent is None else RouteDecision(agent=self.agent, ms=5, prompt_tokens=100)

    def paused_for_sec(self):
        return self.paused

    async def warmup(self, agents):
        self.warmups += 1


def _supervisor(mode, router, on_failure="keyword"):
    triggers = {}
    for m in MANIFESTS:
        triggers.setdefault(m.agent, []).extend(m.triggers)
    registry = AgentRegistry()
    for name in ("responder", "system", "tasks", "lookup"):
        registry.register(_Agent(name, triggers.get(name, ())))
    sup = SupervisorAgent(
        agent_registry=registry, client=None, default_agent="responder", llm_router=router,
        routing_mode=mode, router_on_failure=on_failure,
    )
    registry.register(sup)
    return sup


def _route(sup, text):
    ctx = AgentContext(user_message=text)
    agent = asyncio.run(sup._pick(ctx))
    return agent.name, ctx.metadata.get("routed_via")


# ---- keyword: the rules only ---------------------------------------------------------------------

def test_keyword_mode_never_calls_the_model():
    router = _Router("lookup")
    sup = _supervisor("keyword", router)
    assert _route(sup, "what are my tasks") == ("tasks", "rules")
    assert _route(sup, "do I need an umbrella today") == ("responder", "default")  # no rule: chat, no model
    assert router.calls == []


# ---- model: the router decides everything --------------------------------------------------------

def test_model_mode_asks_the_model_even_when_a_rule_matches():
    router = _Router("responder")
    agent, via = _route(_supervisor("model", router), "what are my tasks")
    assert agent == "responder" and via.startswith("llm")
    assert router.calls == ["what are my tasks"]


def test_model_mode_routes_what_the_rules_miss():
    router = _Router("lookup")
    assert _route(_supervisor("model", router), "do I need an umbrella today")[0] == "lookup"


def test_model_mode_falls_back_to_the_keyword_rules_when_the_model_cannot_answer():
    router = _Router(None)
    assert _route(_supervisor("model", router), "what are my tasks") == ("tasks", "fallback-rules")
    assert _route(_supervisor("model", router), "tell me a joke") == ("responder", "fallback-default")


def test_on_failure_chat_skips_the_rules_fallback():
    router = _Router(None)
    assert _route(_supervisor("model", router, on_failure="chat"), "what are my tasks") == ("responder", "fallback-default")


def test_model_mode_without_a_router_behaves_like_the_fallback():
    assert _route(_supervisor("model", None), "what are my tasks") == ("tasks", "fallback-rules")


def test_a_model_answer_naming_an_unregistered_agent_is_ignored():
    assert _route(_supervisor("model", _Router("memory")), "what are my tasks")[1] == "fallback-rules"


# ---- hybrid: rules when SURE, the model otherwise -------------------------------------------------

def test_hybrid_trusts_a_sure_rule_without_calling_the_model():
    router = _Router("responder")
    assert _route(_supervisor("hybrid", router), "what are my tasks") == ("tasks", "rules")
    assert router.calls == []


@pytest.mark.parametrize("text", [
    "do I need an umbrella today",                     # no rule matched
    "i didn't ask about the weather, what is 1+1",    # a correction cue: the trigger word is only mentioned
    "show the weather and my tasks",                  # two agents claim it
])
def test_hybrid_asks_the_model_when_the_rules_are_not_sure(text):
    router = _Router("responder")
    agent, via = _route(_supervisor("hybrid", router), text)
    assert agent == "responder" and via.startswith("llm")
    assert router.calls == [text]


def test_hybrid_falls_back_to_the_rules_pick_when_the_model_is_down():
    router = _Router(None)
    assert _route(_supervisor("hybrid", router), "show the weather and my tasks")[1] == "fallback-rules"
    assert _route(_supervisor("hybrid", router), "tell me a joke") == ("responder", "fallback-default")


# ---- the sure/confused judgement itself ------------------------------------------------------------

def _profiles():
    return (
        AgentProfile(name="responder", description="General conversation", model_alias=""),
        AgentProfile(name="tasks", description="to-do list", model_alias="", triggers=(r"\btasks?\b",)),
        AgentProfile(name="lookup", description="live data", model_alias="", triggers=(r"\bweather\b", r"\bcurrent\b")),
    )


@pytest.mark.parametrize("text,sure", [
    ("what are my tasks", True),
    ("weather in pune", True),
    ("what are the current tasks", False),              # tasks AND lookup both claim it
    ("i didn't ask about the weather", False),          # negation
    ("never mind the weather", False),
    ("tell me a joke", False),                          # nothing matched
])
def test_is_confident(text, sure):
    assert is_confident(match_agent_detail(text, _profiles())) is sure


def test_a_word_overlap_only_match_is_never_sure():
    profiles = (
        AgentProfile(name="responder", description="General conversation", model_alias=""),
        AgentProfile(name="lookup", description="Live weather forecasts", model_alias=""),  # no trigger regexes
    )
    detail = match_agent_detail("weather forecasts please", profiles)
    assert detail.agent == "lookup" and detail.via == "overlap"
    assert is_confident(detail) is False


# ---- warm-up ---------------------------------------------------------------------------------------

def test_warm_router_warms_the_model_and_is_a_no_op_without_one():
    router = _Router("lookup")
    asyncio.run(_supervisor("model", router).warm_router())
    assert router.warmups == 1
    asyncio.run(_supervisor("keyword", None).warm_router())  # must not raise


# ---- config ----------------------------------------------------------------------------------------

def test_the_routing_config_defaults_are_safe():
    cfg = RoutingConfig()
    assert cfg.mode == "keyword" and cfg.on_failure == "keyword"
    assert cfg.model.model_path and cfg.model.timeout_sec > 0


def test_an_unknown_routing_mode_is_rejected():
    with pytest.raises(Exception):
        RoutingConfig(mode="telepathy")


def test_the_shipped_routing_yaml_is_valid():
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "routing.yaml").read_text(encoding="utf-8"))
    cfg = RoutingConfig(**raw)
    assert cfg.mode in ("keyword", "hybrid", "model")


# ---- keeping the evaluation honest -------------------------------------------------------------------

def test_no_golden_sentence_is_an_example_in_the_router_prompt():
    """The router prompt is tuned on made-up phrasings; if a golden sentence leaked in, its accuracy would be inflated."""
    root = Path(__file__).resolve().parents[1]
    prompt = (root / "config" / "prompts" / "router.md").read_text(encoding="utf-8").lower()
    golden = yaml.safe_load((root / "tests" / "eval" / "golden_set.yaml").read_text(encoding="utf-8"))
    leaked = [g["say"] for g in golden if f"message: {g['say'].lower()}\n" in prompt]
    assert leaked == []


# ---- the breaker state is visible: routing_status() and /api/health -------------------------------------

def test_routing_status_reports_mode_model_and_pause():
    router = _Router("lookup")
    sup = _supervisor("model", router)
    assert sup.routing_status() == {"mode": "model", "router_model": True, "router_paused_for_sec": 0}
    router.paused = 541.7
    assert sup.routing_status()["router_paused_for_sec"] == 541
    assert _supervisor("keyword", None).routing_status() == {"mode": "keyword", "router_model": False, "router_paused_for_sec": 0}
    assert _supervisor(None, None).routing_status()["mode"] == "legacy"


def _health_body(sup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from controller.routes import health

    app = FastAPI()
    app.include_router(health.router)
    app.state.supervisor = sup
    return TestClient(app).get("/health").json()


def test_health_shows_routing_and_turns_degraded_while_the_router_is_paused():
    router = _Router("lookup")
    sup = _supervisor("model", router)
    body = _health_body(sup)
    assert body["routing"] == {"mode": "model", "router_model": True, "router_paused_for_sec": 0}
    router.paused = 300.0
    paused = _health_body(sup)
    assert paused["routing"]["router_paused_for_sec"] == 300 and paused["status"] == "degraded"
