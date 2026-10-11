"""The `remember` tool: lasting facts about the user, saved on request and always in the chat prompt.

Regression: the user said their favourite sweet was gulab jamun; two sessions later Veda answered with a
different favourite. The statement only survived inside a long conversation summary, which similarity
search found unreliably. Facts are now saved as short "topic: value" lines and always shown to the model.

No server or model needed. Run: pytest tests/test_remember.py
"""

import asyncio
from types import SimpleNamespace

import pytest

from domain.entities.agent_context import AgentContext
from domain.policies.fact_policy import format_fact, is_sensitive, normalise_topic, split_fact
from service.agent.responder import ResponderAgent
from service.agent.tool_use_guard import ToolUseGuard
from service.memory.knowledge_base import KnowledgeBase
from service.skills.builtin.remember import RememberSkill
from tpa.filestore.json_knowledge_store import JsonKnowledgeStore
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

MANIFESTS = YamlToolManifestStore().load_all()
_MANIFEST = next(m for m in MANIFESTS if m.name == "remember")


def _kb(tmp_path) -> KnowledgeBase:
    return KnowledgeBase(store=JsonKnowledgeStore(tmp_path / "knowledge.json"))


def _run(skill, **params):
    return asyncio.run(skill.execute(AgentContext(user_message="x"), **params))


# ---- topic policy ------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("favourite sweet", "My favorite sweet"),
    ("fav sweet", "favourite sweet"),
    ("my most fav noodles", "favourite noodles"),
])
def test_equivalent_topics_share_one_key(a, b):
    assert normalise_topic(a) == normalise_topic(b)


def test_a_fact_round_trips_through_its_text_form():
    assert split_fact(format_fact("My favorite sweet", "gulab  jamun")) == ("favourite sweet", "gulab jamun")


def test_free_text_facts_have_no_topic():
    assert split_fact("The dog is called Rex") is None


@pytest.mark.parametrize("text", ["my bank account number is 1234", "the wifi password", "CVV 123", "api key abc"])
def test_sensitive_text_is_detected(text):
    assert is_sensitive(text)


# ---- the skill ---------------------------------------------------------------------------------

def test_save_stores_a_keyed_fact_and_confirms_from_what_was_written(tmp_path):
    kb = _kb(tmp_path)
    r = _run(RememberSkill(kb, _MANIFEST), action="save", topic="favourite sweet", value="gulab jamun")
    assert r.success
    assert kb.list_facts() == ["favourite sweet: gulab jamun"]
    assert "gulab jamun" in r.metadata["spoken"] and "favourite sweet" in r.metadata["spoken"]


def test_a_new_value_for_the_same_topic_replaces_the_old_one(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    _run(skill, action="save", topic="favourite sweet", value="rasgulla")
    r = _run(skill, action="save", topic="My favorite sweet", value="gulab jamun")   # spelled differently
    assert kb.list_facts() == ["favourite sweet: gulab jamun"]
    assert "rasgulla" in r.metadata["spoken"]            # tells the user what it replaced


def test_saving_the_same_thing_twice_says_so(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    _run(skill, action="save", topic="favourite sweet", value="gulab jamun")
    r = _run(skill, action="save", topic="favourite sweet", value="Gulab Jamun")
    assert r.success and len(kb.list_facts()) == 1 and "already" in r.metadata["spoken"]


def test_other_topics_are_left_alone(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    _run(skill, action="save", topic="favourite noodles", value="Maggi")
    _run(skill, action="save", topic="favourite sweet", value="gulab jamun")
    assert sorted(kb.list_facts()) == ["favourite noodles: Maggi", "favourite sweet: gulab jamun"]


def test_forget_removes_only_that_topic(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    _run(skill, action="save", topic="favourite noodles", value="Maggi")
    _run(skill, action="save", topic="favourite sweet", value="gulab jamun")
    assert _run(skill, action="forget", topic="fav sweet").success
    assert kb.list_facts() == ["favourite noodles: Maggi"]


def test_forgetting_something_unknown_changes_nothing(tmp_path):
    kb = _kb(tmp_path)
    r = _run(RememberSkill(kb, _MANIFEST), action="forget", topic="favourite sweet")
    assert r.success and "don't have anything" in r.metadata["spoken"] and kb.list_facts() == []


def test_list_reads_back_what_is_saved(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    assert "haven't saved anything" in _run(skill, action="list").metadata["spoken"]
    _run(skill, action="save", topic="allergy", value="peanuts")
    assert "allergy: peanuts" in _run(skill, action="list").metadata["spoken"]


def test_passwords_and_card_numbers_are_refused(tmp_path):
    kb = _kb(tmp_path)
    r = _run(RememberSkill(kb, _MANIFEST), action="save", topic="bank account number", value="1234 5678")
    assert not r.success and kb.list_facts() == []


def test_missing_or_oversized_input_is_rejected(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    assert not _run(skill, action="save", topic="favourite sweet").success
    assert not _run(skill, action="save", value="gulab jamun").success
    assert not _run(skill, action="save", topic="x", value="y" * 500).success
    assert not _run(skill, action="forget").success
    assert kb.list_facts() == []


def test_the_fact_list_is_capped(tmp_path):
    kb = _kb(tmp_path)
    skill = RememberSkill(kb, _MANIFEST)
    from domain.policies.fact_policy import MAX_FACTS
    for i in range(MAX_FACTS):
        assert _run(skill, action="save", topic=f"item {i}", value="v").success
    assert not _run(skill, action="save", topic="one more", value="v").success
    assert _run(skill, action="save", topic="item 0", value="changed").success      # replacing is still allowed


def test_facts_survive_a_restart(tmp_path):
    _run(RememberSkill(_kb(tmp_path), _MANIFEST), action="save", topic="favourite sweet", value="gulab jamun")
    assert _kb(tmp_path).list_facts() == ["favourite sweet: gulab jamun"]      # a fresh store reads the same file


def test_the_change_is_announced_once_per_save(tmp_path):
    calls = []
    kb = KnowledgeBase(store=JsonKnowledgeStore(tmp_path / "k.json"), on_change=lambda: calls.append(1))
    _run(RememberSkill(kb, _MANIFEST), action="save", topic="favourite sweet", value="gulab jamun")
    assert len(calls) == 1


# ---- the prompt --------------------------------------------------------------------------------

def _responder(kb):
    conversation = SimpleNamespace(turns=[], get_summaries_block=lambda: "")
    return ResponderAgent(client=None, conversation=conversation, knowledge=kb)


def test_saved_facts_are_always_in_the_chat_prompt(tmp_path):
    kb = _kb(tmp_path)
    kb.remember("favourite sweet", "gulab jamun")
    prompt = _responder(kb).build_prompt(AgentContext(user_message="and my dessert?"))
    assert "favourite sweet: gulab jamun" in prompt


def test_facts_stay_in_the_prompt_when_recalled_summaries_are_added(tmp_path):
    kb = _kb(tmp_path)
    kb.remember("favourite sweet", "gulab jamun")
    ctx = AgentContext(user_message="and my dessert?")
    ctx.metadata["semantic_knowledge_context"] = "RELEVANT THINGS I REMEMBER:\n- User asked about Paris."
    prompt = _responder(kb).build_prompt(ctx)
    assert "favourite sweet: gulab jamun" in prompt and "asked about Paris" in prompt


# ---- the claims guard ---------------------------------------------------------------------------

def test_a_claim_to_have_saved_something_is_flagged_but_ordinary_talk_is_not():
    guard = ToolUseGuard(MANIFESTS)
    assert guard.claims_action("Got it, I'll remember that your favourite sweet is gulab jamun.")
    assert guard.claims_action("I've saved that.")
    assert not guard.claims_action("I remember you like gulab jamun.")
    assert not guard.claims_action("Your favourite sweet is gulab jamun.")


def test_saved_facts_are_shown_as_the_users_not_veda_s(tmp_path):
    # Regression: "what are fav foods?" was answered "My favorite foods are Maggi and gulab jamun" - as if
    # they were Veda's own favourites - because the facts were shown without saying whose they were.
    kb = _kb(tmp_path)
    kb.remember("favourite sweet", "gulab jamun")
    kb.store.add_fact("lives in Hyderabad")                              # a free-text fact keeps its wording
    block = kb.get_context()
    assert "the user's favourite sweet: gulab jamun" in block and "- lives in Hyderabad" in block
    assert "not mine" in block and '"your"' in block
