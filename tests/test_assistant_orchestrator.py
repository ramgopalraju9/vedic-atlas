"""Phase 3 (docs/10): the orchestrator's turn flow, with a scripted decoder and fake skills (no model)."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.entities.agent_result import AgentResult
from domain.entities.control_decision import ControlDecision
from domain.entities.skill_result import SkillResult
from domain.policies.dispatch_policy import ONE_AT_A_TIME_REPLY, REFUSAL_REPLY, UNPARSEABLE_REPLY
from domain.policies.reply_policy import UNCONFIRMED_REPLY
from service.agent.assistant_orchestrator import AssistantOrchestrator
from service.agent.base_agent import BaseAgent
from service.agent.control_decoder import ControlDecoder, DecodeResult, parse_decision
from service.agent.supervisor import SupervisorAgent
from service.prompting.prompt_composer import PromptComposer
from service.session.session_state import SessionStateService
from tpa.filestore.file_prompt_store import FilePromptStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.persistence.models import session_context, turn_trace  # noqa: F401 (register tables)
from tpa.persistence.repositories.session_context_repository import SqliteSessionContextRepository
from tpa.persistence.repositories.trace_repository import SqliteTraceRepository
from tpa.persistence.session import Base

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
NOW = datetime(2026, 10, 7, 14, 30)


def call(tool, **args):
    return {"tool": tool, "args": args}


def dec(calls=(), live=None, clar=None):
    return ControlDecision(calls=tuple(calls), needs_live_data=live, clarification=clar)


class ScriptedDecoder:
    """Returns the next scripted decision and records what it was shown."""

    def __init__(self, *decisions):
        self.decisions, self.seen = list(decisions), []

    async def decide(self, user_message, history=(), active=""):
        self.seen.append({"message": user_message, "active": active, "history": list(history)})
        return DecodeResult(decision=self.decisions.pop(0), prompt_tokens=900, ms=7)


class FakeSkills:
    def __init__(self, outputs):
        self.outputs, self.ran = outputs, []

    async def execute_skill(self, ctx, name, **args):
        self.ran.append((name, args))
        out = self.outputs[name]
        if isinstance(out, Exception):
            raise out
        return out(args) if callable(out) else out


class Convo:
    def __init__(self):
        self.turns, self.added, self.resolved = [], [], 0

    def current_session_id(self):
        self.resolved += 1
        return "sess-1"

    def add_turn(self, role, content, session_id=None):
        self.added.append((role, content, session_id))


class FakeResponder(BaseAgent):
    def __init__(self, text="Chat reply."):
        super().__init__(name="responder", description="chat")
        self.text, self.ran = text, 0

    async def execute(self, ctx):
        self.ran += 1
        return AgentResult(agent_name="responder", response=self.text)

    async def execute_stream(self, ctx, cancel_event=None):
        self.ran += 1
        yield self.text


class FakeClient:
    def __init__(self, text="Narrated."):
        self.text, self.calls = text, 0

    async def complete(self, **kw):
        self.calls += 1
        return self.text


def ok(spoken=None, observation="obs", final=False):
    return SkillResult(skill_name="x", success=True, output=observation, metadata={"spoken": spoken, "final": final})


def fail(error="provider down"):
    return SkillResult(skill_name="x", success=False, error=error)


def env(decisions, skills, *, responder_text="Chat reply.", narration="Narrated.", claims=lambda t: False):
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    session_state = SessionStateService(SqliteSessionContextRepository(session_factory=factory), MANIFESTS, now=lambda: NOW)
    traces = SqliteTraceRepository(session_factory=factory)
    decoder, runner, convo = ScriptedDecoder(*decisions), FakeSkills(skills), Convo()
    responder, client = FakeResponder(responder_text), FakeClient(narration)
    orch = AssistantOrchestrator(
        decoder=decoder, composer=PromptComposer(FilePromptStore(), list(MANIFESTS.values())), skill_runner=runner,
        manifests=MANIFESTS, responder=responder, client=client, conversation=convo, claims_action=claims,
        session_state=session_state, traces=traces, grounding_check=lambda *a: True,
    )
    return SimpleNamespace(orch=orch, decoder=decoder, runner=runner, convo=convo, responder=responder,
                           client=client, state=session_state, traces=traces)


def turn(e, message, stream=False):
    ctx = AgentContext(user_message=message)
    if stream:
        async def run():
            return "".join([c async for c in e.orch.execute_stream(ctx)])
        return ctx, asyncio.run(run())
    return ctx, asyncio.run(e.orch.execute(ctx))


WEATHER = lambda a: ok(spoken=f"In {a.get('place') or 'Hyderabad'} it's 18 degrees.")


# ---- the two original bugs ---------------------------------------------------

def test_follow_up_reuses_the_active_place_from_the_previous_turn():
    e = env([dec([call("get_weather", place="Tokyo")], live=True), dec([call("get_weather", place="Tokyo")], live=True)],
            {"get_weather": WEATHER})
    turn(e, "what's the weather in Tokyo")
    assert e.decoder.seen[0]["active"] == ""                                   # nothing active on the first turn
    _, result = turn(e, "should I bring an umbrella?")
    assert e.decoder.seen[1]["active"] == "ACTIVE: get_weather | place=Tokyo | just now"
    assert result.response == "In Tokyo it's 18 degrees." and e.responder.ran == 0


def test_mention_without_request_goes_to_chat_and_no_tool_runs():
    e = env([dec(live=False)], {"get_weather": WEATHER}, responder_text="2")
    _, result = turn(e, "I didn't ask about Singapore weather, what's 1+1?")
    assert result.response == "2" and e.runner.ran == [] and e.responder.ran == 1
    assert e.client.calls == 0                                                 # no content decode either


# ---- dispatch routes ---------------------------------------------------------

def test_live_data_with_no_tool_is_refused_and_chat_is_not_consulted():
    e = env([dec(live=True)], {})
    _, result = turn(e, "what's on my calendar")
    assert result.response == REFUSAL_REPLY and e.responder.ran == 0 and e.runner.ran == []


def test_clarification_is_asked_saved_and_runs_nothing():
    e = env([dec(clar="Which task do you mean?")], {})
    ctx, result = turn(e, "delete it")
    assert result.response == "Which task do you mean?" and e.runner.ran == []
    assert ("user", "delete it", "sess-1") in e.convo.added and ("assistant", "Which task do you mean?", "sess-1") in e.convo.added


def test_invalid_decode_fails_closed_instead_of_guessing():
    e = env([ControlDecision(valid=False, note="truncated")], {})
    _, result = turn(e, "anything")
    assert result.response == UNPARSEABLE_REPLY and e.responder.ran == 0 and e.runner.ran == []


def test_destructive_call_in_a_multi_call_is_not_executed():
    e = env([dec([call("get_weather", place="Pune"), call("tasks", action="delete", title="bank")])], {"get_weather": WEATHER})
    _, result = turn(e, "weather in Pune and delete the bank task")
    assert result.response == ONE_AT_A_TIME_REPLY and e.runner.ran == []


def test_orchestration_bug_returns_an_error_reply_not_an_exception():
    class Boom:
        async def decide(self, *a, **k): raise RuntimeError("bug")
    e = env([], {})
    e.orch._decoder = Boom()
    _, result = turn(e, "hi")
    assert "went wrong" in result.response


# ---- tools: reply assembly ---------------------------------------------------

def test_multi_intent_runs_both_in_order_and_joins_template_sentences_verbatim_with_no_model_call():
    e = env([dec([call("get_weather", place="Tokyo"), call("convert_currency", amount=100, **{"from": "USD", "to": "INR"})], live=True)],
            {"get_weather": WEATHER, "convert_currency": ok(spoken="100 USD is about 8300 INR.")})
    _, result = turn(e, "weather in Tokyo and convert 100 USD to INR")
    assert result.response == "In Tokyo it's 18 degrees. 100 USD is about 8300 INR."
    assert [n for n, _ in e.runner.ran] == ["get_weather", "convert_currency"] and e.client.calls == 0


def test_only_results_that_need_narrating_reach_the_model_and_share_one_decode():
    e = env([dec([call("web_search", query="IPL"), call("get_weather", place="Tokyo"), call("web_search", query="ISRO")], live=True)],
            {"web_search": ok(observation="SNIPPET about the topic"), "get_weather": WEATHER}, narration="Here is the news.")
    _, result = turn(e, "search IPL, weather Tokyo, search ISRO")
    assert e.client.calls == 1                                                  # one content decode for both searches
    assert result.response == "Here is the news. In Tokyo it's 18 degrees."     # spliced at the first narrated position


def test_failed_call_gets_plain_failure_text_never_an_invented_result():
    e = env([dec([call("get_weather", place="Tokyo"), call("convert_currency", amount=5, **{"from": "USD", "to": "EUR"})], live=True)],
            {"get_weather": fail("provider down"), "convert_currency": ok(spoken="5 USD is 4.3 EUR.")})
    _, result = turn(e, "x")
    assert result.response == "I couldn't do that: provider down 5 USD is 4.3 EUR."


def test_a_failed_call_that_carries_its_own_sentence_is_spoken_with_that_sentence():
    failed = SkillResult(skill_name="x", success=False, error="no app name", metadata={"spoken": "Which app do you mean?"})
    e = env([dec([call("get_weather", place="Tokyo")], live=True)], {"get_weather": failed})
    _, result = turn(e, "x")
    assert result.response == "Which app do you mean?"


def test_a_skill_that_raises_is_a_failed_call_not_a_crash():
    e = env([dec([call("get_weather", place="Tokyo")], live=True)], {"get_weather": RuntimeError("boom")})
    _, result = turn(e, "x")
    assert result.response == "I couldn't do that: boom"


def test_ungrounded_or_failed_narration_falls_back_to_the_tool_text():
    e = env([dec([call("web_search", query="x")], live=True)], {"web_search": ok(observation="first line\nsecond")}, narration="")
    _, result = turn(e, "x")
    assert result.response == "first line"


def test_narration_input_is_capped_by_the_tools_max_result_tokens():
    seen = {}
    e = env([dec([call("web_search", query="x")], live=True)], {"web_search": ok(observation="word " * 3000)})
    orig = e.orch._composer.narrate_stage
    e.orch._composer.narrate_stage = lambda q, results: seen.setdefault("n", len(results[0])) and orig(q, results)
    turn(e, "x")
    assert seen["n"] < 3000 * 5 / 2                                             # cut well below the raw 15000 chars


# ---- backstop ----------------------------------------------------------------

def test_claim_with_no_successful_tool_is_replaced_on_the_clarify_path_too():
    e = env([dec(clar="Task added to your list.")], {}, claims=lambda t: "task added" in t.lower())
    _, result = turn(e, "x")
    assert result.response == UNCONFIRMED_REPLY


def test_claim_backed_by_a_successful_tool_is_allowed():
    e = env([dec([call("tasks", action="add", title="milk")])], {"tasks": ok(spoken="Task added: milk.")},
            claims=lambda t: "task added" in t.lower())
    _, result = turn(e, "add milk")
    assert result.response == "Task added: milk."


# ---- session state, persistence, trace ---------------------------------------

def test_session_id_is_resolved_once_and_state_is_written_only_after_success():
    e = env([dec([call("get_weather", place="Tokyo")], live=True), dec([call("get_weather", place="Oslo")], live=True)],
            {"get_weather": WEATHER})
    ctx, _ = turn(e, "weather in Tokyo")
    assert e.convo.resolved == 1 and ctx.session_id == "sess-1"
    assert e.state.get("sess-1").slots == {"place": "Tokyo"}
    e.runner.outputs["get_weather"] = fail()
    turn(e, "weather in Oslo")
    assert e.state.get("sess-1").slots == {"place": "Tokyo"}                     # a failed call never overwrites


def test_destructive_call_runs_but_never_becomes_state():
    e = env([dec([call("tasks", action="delete", title="bank")])], {"tasks": ok(spoken="Removed.")})
    turn(e, "delete the bank task")
    assert e.state.get("sess-1") is None


def test_trace_records_the_new_fields():
    e = env([dec([call("get_weather", place="Tokyo")], live=True), dec([call("get_weather", place="Tokyo")], live=True)],
            {"get_weather": WEATHER})
    turn(e, "weather in Tokyo")
    turn(e, "umbrella?")
    t = e.traces.recent(1)[0]
    assert (t.agent, t.decided, t.state_used, t.needs_live_data, t.clarified) == ("assistant", "tool", True, True, False)
    assert t.slots_inherited == ["place"] and t.prompt_tokens["control"] == 900 and t.timings_ms["control"] == 7
    turn_clar = env([dec(clar="Which one?")], {})
    turn(turn_clar, "delete it")
    assert turn_clar.traces.recent(1)[0].clarified is True and turn_clar.traces.recent(1)[0].decided == "clarify"


def test_exactly_one_control_decode_per_turn():
    e = env([dec(live=False), dec([call("get_weather", place="Tokyo")], live=True)], {"get_weather": WEATHER})
    turn(e, "chat")
    turn(e, "weather")
    assert len(e.decoder.seen) == 2


# ---- streaming + supervisor delegation ---------------------------------------

def test_stream_of_a_tool_turn_emits_the_reply_once_and_chat_streams_through_the_responder():
    e = env([dec([call("get_weather", place="Tokyo")], live=True), dec(live=False)], {"get_weather": WEATHER}, responder_text="Hi!")
    _, tool_text = turn(e, "weather", stream=True)
    _, chat_text = turn(e, "hello", stream=True)
    assert tool_text == "In Tokyo it's 18 degrees." and chat_text == "Hi!"


def test_supervisor_delegates_every_turn_to_the_orchestrator():
    e = env([dec(live=False)], {}, responder_text="Chat!")
    sup = SupervisorAgent(orchestrator=e.orch)
    result = asyncio.run(sup.execute(AgentContext(user_message="hello")))
    assert result.response == "Chat!" and result.delegated_to == "responder"

    e2 = env([dec([call("get_weather", place="Tokyo")], live=True)], {"get_weather": WEATHER})
    sup2 = SupervisorAgent(orchestrator=e2.orch)

    async def run():
        return "".join([c async for c in sup2.execute_stream(AgentContext(user_message="weather"))])

    assert asyncio.run(run()) == "In Tokyo it's 18 degrees."


# ---- decoder -----------------------------------------------------------------

def test_parse_decision_is_strict():
    good = '{"needs_live_data": true, "calls": [{"tool": "get_weather", "args": {}}], "clarification": null}'
    d = parse_decision(good)
    assert d.valid and d.needs_live_data is True and d.calls[0]["tool"] == "get_weather"
    for bad in ("", "not json", '{"calls": []}', '{"needs_live_data": "yes", "calls": [], "clarification": null}',
                '{"needs_live_data": true, "calls": {}, "clarification": null}', '{"needs_live_data": true, "calls": [], "clarification": 3}'):
        assert not parse_decision(bad).valid


def test_decoder_sends_the_constrained_schema_and_never_raises():
    class Client:
        def __init__(self, out): self.out, self.kw = out, None
        async def complete(self, **kw):
            self.kw = kw
            if isinstance(self.out, Exception):
                raise self.out
            return self.out

    composer = PromptComposer(FilePromptStore(), list(MANIFESTS.values()))
    good = Client('{"needs_live_data": false, "calls": [], "clarification": null}')
    r = asyncio.run(ControlDecoder(client=good, composer=composer, manifests=MANIFESTS).decide("hi"))
    assert r.decision.valid and list(good.kw["json_schema"]["properties"])[0] == "needs_live_data" and r.prompt_tokens > 0
    assert good.kw["system"] == composer.control_stage("other").system         # the cached prefix

    boom = Client(RuntimeError("model down"))
    r = asyncio.run(ControlDecoder(client=boom, composer=composer, manifests=MANIFESTS).decide("hi"))
    assert not r.decision.valid and "model down" in r.decision.note


def test_decoder_defaults_are_the_measured_winners():
    """Spike A5: temperature 0 and explicit `ACTIVE: none` / `RECENT: none` scored best (docs/10 §6c)."""
    class Client:
        kw = None
        async def complete(self, **kw):
            self.kw = kw
            return '{"needs_live_data": false, "calls": [], "clarification": null}'

    c = Client()
    composer = PromptComposer(FilePromptStore(), list(MANIFESTS.values()))
    asyncio.run(ControlDecoder(client=c, composer=composer, manifests=MANIFESTS).decide("hello there"))
    assert c.kw["temperature"] == 0.0
    assert "ACTIVE: none" in c.kw["prompt"] and "RECENT: none" in c.kw["prompt"]


# ---- data plane: a document never reaches a control prompt (docs/10 Phase 5) -----------------------------

def test_the_control_prompt_has_no_input_for_a_tool_result():
    import inspect
    params = set(inspect.signature(PromptComposer.control_stage).parameters) - {"self"}
    assert params == {"user_message", "history", "active", "exchanges", "key_order", "show_empty"}


def test_a_document_cannot_leak_through_history_or_state_when_narration_fails():
    doc = "SECRETDOC " * 3000                                  # one huge line, as an email body would be
    e = env([dec([call("web_search", query="x")], live=True)], {"web_search": ok(observation=doc)}, narration="")
    _, result = turn(e, "search x")
    saved = e.convo.added[-1][1]
    assert saved == result.response
    assert len(saved) < 400 and saved != doc                    # capped fallback, not the document
    assert "SECRETDOC" not in e.state.active_line("sess-1")     # session state only ever holds validated ARGUMENTS


def test_a_document_result_is_capped_before_the_content_decode():
    seen = {}
    e = env([dec([call("web_search", query="x")], live=True)], {"web_search": ok(observation="word " * 3000)})
    orig = e.orch._composer.content_stage
    e.orch._composer.content_stage = lambda task, doc, q, **kw: seen.setdefault("task", task) and orig(task, doc, q, **kw)
    turn(e, "x")
    assert seen["task"] == "narrate"
