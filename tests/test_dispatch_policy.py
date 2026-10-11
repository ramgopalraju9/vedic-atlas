"""Phase 3 (docs/10): the pure rules that turn a ControlDecision into an action and a reply."""

import asyncio

import pytest
from pydantic import ValidationError

from domain.entities.conversation import Turn
from domain.entities.control_decision import ControlDecision
from domain.policies.claim_guard_policy import SentenceClaimGuard
from domain.policies.dispatch_policy import (
    ONE_AT_A_TIME_REPLY, REFUSAL_REPLY, UNPARSEABLE_REPLY, Route, resolve,
)
from domain.policies.reply_policy import FAILED, NARRATE, SPOKEN, classify_call, failure_text, spoken_text
from schemas.tool_manifest_schema import ToolManifestSchema
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}


def call(tool, **args):
    return {"tool": tool, "args": args}


def dec(calls=(), live=None, clar=None, valid=True):
    return ControlDecision(calls=tuple(calls), needs_live_data=live, clarification=clar, valid=valid)


# ---- dispatch ----------------------------------------------------------------

def test_calls_run_tools_and_ignore_a_stray_clarification():
    r = resolve(dec([call("get_weather", place="Tokyo")], live=True, clar="Which city?"), MANIFESTS)
    assert r.route is Route.TOOLS and r.calls == (call("get_weather", place="Tokyo"),)


def test_clarification_without_calls_asks_and_trims():
    r = resolve(dec(clar="  Which task do you mean? "), MANIFESTS)
    assert (r.route, r.text) == (Route.CLARIFY, "Which task do you mean?")


def test_blank_clarification_is_not_a_clarification():
    assert resolve(dec(live=False, clar="   "), MANIFESTS).route is Route.CHAT


def test_live_data_with_no_call_is_a_fixed_refusal_never_chat():
    r = resolve(dec(live=True), MANIFESTS)
    assert (r.route, r.text) == (Route.REFUSE, REFUSAL_REPLY)


def test_no_call_and_not_live_goes_to_chat_and_a_missing_flag_also_chats():
    assert resolve(dec(live=False), MANIFESTS).route is Route.CHAT
    assert resolve(dec(live=None), MANIFESTS).route is Route.CHAT


def test_invalid_output_fails_closed():
    r = resolve(dec(valid=False), MANIFESTS)
    assert (r.route, r.text) == (Route.FAIL_CLOSED, UNPARSEABLE_REPLY)


def test_calls_naming_no_real_tool_fail_closed_rather_than_chat():
    assert resolve(dec([call("rm_rf")]), MANIFESTS).route is Route.FAIL_CLOSED
    assert resolve(dec([{"tool": "get_weather", "args": "Tokyo"}]), MANIFESTS).route is Route.FAIL_CLOSED


def test_unusable_calls_are_dropped_but_good_ones_still_run():
    r = resolve(dec([call("rm_rf"), call("get_weather", place="Pune")]), MANIFESTS)
    assert r.route is Route.TOOLS and [c["tool"] for c in r.calls] == ["get_weather"]


def test_duplicates_are_removed_and_calls_capped():
    same = call("get_weather", place="Pune")
    assert resolve(dec([same, same]), MANIFESTS).calls == (same,)
    many = [call("get_weather", place=p) for p in "ABCDE"]
    assert len(resolve(dec(many), MANIFESTS).calls) == 3
    assert len(resolve(dec(many), MANIFESTS, max_calls=2).calls) == 2


def test_destructive_call_never_joins_a_multi_call():
    r = resolve(dec([call("get_weather", place="Pune"), call("tasks", action="delete", title="bank")]), MANIFESTS)
    assert (r.route, r.text, r.calls) == (Route.CLARIFY, ONE_AT_A_TIME_REPLY, ())


def test_a_lone_destructive_call_still_runs_it_is_the_approval_hook_that_guards_it():
    r = resolve(dec([call("tasks", action="delete", title="bank")]), MANIFESTS)
    assert r.route is Route.TOOLS


def test_a_safe_multi_call_runs():
    r = resolve(dec([call("get_weather", place="Tokyo"), call("tasks", action="add", title="milk")]), MANIFESTS)
    assert r.route is Route.TOOLS and len(r.calls) == 2


# ---- reply policy ------------------------------------------------------------

def test_classify_call():
    assert classify_call(ok=False, reply_mode="template", final=True) == FAILED
    assert classify_call(ok=True, reply_mode="template", final=False) == SPOKEN
    assert classify_call(ok=True, reply_mode="llm", final=True) == SPOKEN
    assert classify_call(ok=True, reply_mode="llm", final=False) == NARRATE


def test_failure_and_spoken_text():
    assert failure_text("boom") == "I couldn't do that: boom" and failure_text(None) == "I couldn't do that: blocked"
    assert spoken_text("In Tokyo it's 18.", "x") == "In Tokyo it's 18."
    assert spoken_text(None, "first\nsecond") == "first" and spoken_text(None, "") == "Done."


def test_a_document_result_cannot_be_templated():
    base = {"name": "t", "agent": "a", "description": "d"}
    with pytest.raises(ValidationError):
        ToolManifestSchema(**base, returns="document", reply_mode="template")
    assert ToolManifestSchema(**base, returns="document", reply_mode="llm").to_domain().returns == "document"


# ---- streamed claim guard ----------------------------------------------------

def is_claim(t):
    return "task added" in t.lower()


def feed_all(guard, chunks):
    out = []
    for c in chunks:
        out += guard.feed(c)
    return out + guard.finish()


def test_guard_releases_only_whole_sentences_even_across_chunk_boundaries():
    g = SentenceClaimGuard(is_claim)
    assert g.feed("Hello the") == [] and g.feed("re. How are") == ["Hello there. "]
    assert g.finish() == ["How are"] and not g.tripped and g.released


def test_guard_trips_on_the_first_claiming_sentence_and_releases_nothing_after():
    g = SentenceClaimGuard(is_claim)
    out = feed_all(g, ["Sure thing. ", "Task ", "added to your list. ", "Anything else? "])
    assert out == ["Sure thing. "] and g.tripped and g.released


def test_guard_that_trips_before_any_release_reports_nothing_released():
    g = SentenceClaimGuard(is_claim)
    assert feed_all(g, ["Task added. ", "More."]) == [] and g.tripped and not g.released


def test_guard_with_a_claim_in_the_unfinished_tail_catches_it_on_finish():
    g = SentenceClaimGuard(is_claim)
    assert g.feed("Fine. Task added") == ["Fine. "] and g.finish() == [] and g.tripped


# ---- responder veto (streaming + non-streaming) ------------------------------

class _Conv:
    turns: list = []
    def __init__(self): self.added = []
    def add_turn(self, role, content, session_id=None): self.added.append((role, content))
    def get_summaries_block(self, limit=None): return ""


class _Knowledge:
    def get_context(self): return ""


class _Client:
    def __init__(self, text): self.text = text; self.cancelled = None
    async def complete(self, **kw): return self.text
    async def stream(self, prompt, system="", model=None, cancel_event=None, **kw):
        self.cancelled = cancel_event
        for i in range(0, len(self.text), 7):
            if cancel_event is not None and cancel_event.is_set():
                return
            yield self.text[i:i + 7]


def _responder(text, veto=is_claim):
    from domain.entities.agent_context import AgentContext
    from service.agent.responder import ResponderAgent
    conv, client = _Conv(), _Client(text)
    return ResponderAgent(client=client, conversation=conv, knowledge=_Knowledge(), reply_veto=veto), conv, client, AgentContext(user_message="hi")


def test_responder_replaces_a_claiming_reply_and_saves_the_replacement_not_the_claim():
    r, conv, _, ctx = _responder("Done. Task added to your list.")
    result = asyncio.run(r.execute(ctx))
    assert "can't confirm" in result.response
    assert all("Task added" not in c for _, c in conv.added) and ("assistant", result.response) in conv.added


def test_responder_without_a_veto_is_unchanged():
    r, conv, _, ctx = _responder("Task added to your list.", veto=None)
    assert asyncio.run(r.execute(ctx)).response == "Task added to your list."


def test_responder_stream_stops_at_the_claim_and_stops_the_model():
    r, conv, client, ctx = _responder("Happy to help. Task added to your list. " + "Here is more text. " * 40)

    async def run():
        return [c async for c in r.execute_stream(ctx)]

    chunks = asyncio.run(run())
    text = "".join(chunks)
    assert "Happy to help." in text and "Task added" not in text and "more text" not in text
    assert client.cancelled.is_set()                                   # the model was told to stop
    assert [role for role, _ in conv.added] == ["user", "assistant"]    # saved exactly once
    assert "Task added" not in conv.added[-1][1] and conv.added[-1][1].startswith("Happy to help")


def test_responder_stream_that_finishes_before_the_trip_still_saves_exactly_once():
    r, conv, _, ctx = _responder("Happy to help. Task added to your list.")

    async def run():
        return "".join([c async for c in r.execute_stream(ctx)])

    assert "Task added" not in asyncio.run(run())
    assert [role for role, _ in conv.added] == ["user", "assistant"] and "Task added" not in conv.added[-1][1]


def test_responder_stream_with_nothing_safe_yields_the_unconfirmed_reply():
    r, conv, _, ctx = _responder("Task added to your list. Done.")

    async def run():
        return "".join([c async for c in r.execute_stream(ctx)])

    assert "can't confirm" in asyncio.run(run())


def test_responder_stream_passes_a_clean_reply_through_and_saves_it_once():
    r, conv, _, ctx = _responder("It's a lovely day. Enjoy it.")

    async def run():
        return "".join([c async for c in r.execute_stream(ctx)])

    assert asyncio.run(run()).strip() == "It's a lovely day. Enjoy it."
    assert [role for role, _ in conv.added] == ["user", "assistant"]


# ---- destructive calls must name their target (fail-closed veto) --------------

from domain.policies.dispatch_policy import TARGET_REQUIRED_REPLY  # noqa: E402
from domain.policies.destructive_policy import target_is_explicit  # noqa: E402

TASKS, REMEMBER = MANIFESTS["tasks"], MANIFESTS["remember"]


def test_manifests_declare_which_argument_is_the_target_and_the_model_cannot_emit_an_id():
    assert TASKS.target_params == ("title",) and REMEMBER.target_params == ("topic",)
    assert "task_id" not in {p.name for p in TASKS.params}          # the model cannot know real ids; it invented task_id=1


def test_explicit_target_is_matched_by_significant_words_of_the_users_message():
    assert target_is_explicit(TASKS, {"action": "delete", "title": "dentist"}, "delete the task about the dentist")
    assert target_is_explicit(TASKS, {"action": "delete", "title": "milk packets"}, "remove the milk task")
    assert target_is_explicit(REMEMBER, {"action": "forget", "topic": "favourite sweet"}, "forget my favourite sweet")
    assert target_is_explicit(TASKS, {"action": "delete", "title": "x"}, "delete x please")             # short value: whole match
    assert not target_is_explicit(TASKS, {"action": "delete", "title": "x"}, "delete the next one")        # "x" inside a word is not a name
    for generic in ("first one", "the task", "the first", "last one", "that task"):                          # ordinals and filler name nothing
        assert not target_is_explicit(TASKS, {"action": "delete", "title": generic}, f"delete {generic}"), generic
    assert not target_is_explicit(TASKS, {"action": "delete", "title": "the dentist"}, "delete the one I told you")  # "the" is not "dentist"
    assert target_is_explicit(TASKS, {"action": "delete", "title": "the bank one"}, "the bank one")
    for pronoun in ("it", "that", "them", "one", "this"):                                                    # the word is in the message, but names nothing
        assert not target_is_explicit(TASKS, {"action": "delete", "title": pronoun}, f"delete {pronoun}"), pronoun
        assert not target_is_explicit(REMEMBER, {"action": "forget", "topic": pronoun}, f"forget {pronoun}"), pronoun


def test_a_guessed_missing_or_unmentioned_target_is_not_explicit():
    assert not target_is_explicit(TASKS, {"action": "delete"}, "delete it")                              # no target at all
    assert not target_is_explicit(TASKS, {"action": "delete", "title": ""}, "delete it")
    assert not target_is_explicit(TASKS, {"action": "delete", "title": "bank"}, "delete it")             # guessed from history
    assert not target_is_explicit(REMEMBER, {"action": "forget", "topic": "dog's name"}, "forget it")


def test_non_destructive_calls_and_tools_without_a_target_rule_are_never_vetoed():
    assert target_is_explicit(TASKS, {"action": "add", "title": "milk"}, "buy something unrelated")
    assert target_is_explicit(TASKS, {"action": "list"}, "anything")
    assert target_is_explicit(MANIFESTS["get_weather"], {"place": "Tokyo"}, "unrelated words")


def test_dispatch_asks_instead_of_running_a_delete_whose_target_was_not_named():
    r = resolve(dec([call("tasks", action="delete", title="bank")]), MANIFESTS, user_message="delete it")
    assert (r.route, r.text, r.calls) == (Route.CLARIFY, TARGET_REQUIRED_REPLY, ())
    r = resolve(dec([call("tasks", action="delete")]), MANIFESTS, user_message="delete it")
    assert r.route is Route.CLARIFY


def test_dispatch_runs_a_delete_the_user_named_and_the_veto_only_applies_when_a_message_is_given():
    named = resolve(dec([call("tasks", action="delete", title="dentist")]), MANIFESTS, user_message="delete the dentist task")
    assert named.route is Route.TOOLS
    assert resolve(dec([call("tasks", action="delete", title="bank")]), MANIFESTS).route is Route.TOOLS   # no message supplied
    safe = resolve(dec([call("tasks", action="add", title="milk")]), MANIFESTS, user_message="completely different words")
    assert safe.route is Route.TOOLS


# ---- a pronoun is not a target, even for a non-destructive write ----------------------------------------------

from domain.policies.destructive_policy import target_names_nothing  # noqa: E402


def test_a_pronoun_or_ordinal_target_names_nothing_for_any_call_with_a_target():
    for title in ("it", "that", "first one", "the task", "them"):
        assert target_names_nothing(TASKS, {"action": "complete", "title": title}), title
    assert not target_names_nothing(TASKS, {"action": "complete", "title": "milk"})        # inherited real name: fine
    assert not target_names_nothing(TASKS, {"action": "list"})                              # no target at all: fine
    assert not target_names_nothing(MANIFESTS["get_weather"], {"place": "it"})     # no target_params: not this rule


def test_dispatch_asks_instead_of_completing_a_task_called_it():
    from domain.entities.control_decision import ControlDecision
    from domain.policies.dispatch_policy import Route, TARGET_REQUIRED_REPLY, resolve
    d = ControlDecision(calls=({"tool": "tasks", "args": {"action": "complete", "title": "it"}},), needs_live_data=True)
    r = resolve(d, MANIFESTS, user_message="mark it as done")
    assert (r.route, r.text) == (Route.CLARIFY, TARGET_REQUIRED_REPLY)
    ok = ControlDecision(calls=({"tool": "tasks", "args": {"action": "complete", "title": "milk"}},), needs_live_data=True)
    assert resolve(ok, MANIFESTS, user_message="mark that one done").route == Route.TOOLS
