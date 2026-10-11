"""The recall rule: a score floor, a margin around the best hit, a prompt-size budget, dates, and follow-up search.
Fake store and fake embedding, so the numbers below are exactly the cases the rule was designed around."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from domain.entities.agent_context import AgentContext
from domain.value_objects.memory_hit import MemoryHit
from service.agent.responder import ResponderAgent
from service.memory.semantic_recall import SemanticRecall


def run(coro):
    return asyncio.run(coro)


class Embedding:
    model_id = "m"

    async def embed(self, text):
        return SimpleNamespace(vector=(1.0,))


class Store:
    def __init__(self, hits):
        self.hits, self.queries = hits, 0

    def search(self, *, vector, model_id, top_k, sources):
        self.queries += 1
        return sorted(self.hits, key=lambda h: h.score, reverse=True)[:top_k]


def hit(ref, score, text="User asked about plans. Veda answered.", source="summary"):
    return MemoryHit(source=source, ref_id=str(ref), text=text, score=score)


def recall(hits, **kw):
    params = dict(top_k=4, min_score=0.62, sources=("summary",), margin=0.05)
    params.update(kw)
    return SemanticRecall(Store(hits), Embedding(), **params)


# ---- the floor -----------------------------------------------------------------------------------------------------------------

def test_nothing_is_injected_when_even_the_best_match_is_below_the_floor():
    assert run(recall([hit(1, 0.56), hit(2, 0.50)]).recall("what is 2 plus 2")) == []


def test_a_match_at_or_above_the_floor_is_kept():
    assert [h.ref_id for h in run(recall([hit(1, 0.66)]).recall("q"))] == ["1"]
    assert [h.ref_id for h in run(recall([hit(1, 0.62)]).recall("q"))] == ["1"]


# ---- the margin ------------------------------------------------------------------------------------------------------------------

def test_hits_close_behind_the_best_are_kept_and_far_ones_dropped():
    got = run(recall([hit(1, 0.80), hit(2, 0.77), hit(3, 0.76), hit(4, 0.70)]).recall("q"))
    assert [h.ref_id for h in got] == ["1", "2", "3"]          # 0.70 is 0.10 behind: dropped


def test_without_a_margin_every_hit_over_the_floor_is_kept():
    got = run(recall([hit(1, 0.80), hit(2, 0.70)], margin=None).recall("q"))
    assert [h.ref_id for h in got] == ["1", "2"]


def test_the_floor_applies_before_the_margin():
    assert [h.ref_id for h in run(recall([hit(1, 0.64), hit(2, 0.60)]).recall("q"))] == ["1"]


# ---- the block: dates, order, budget ---------------------------------------------------------------------------------------------

def dated(days):
    return lambda h: days.get(h.ref_id)


def test_the_block_is_dated_and_shows_the_newest_first():
    rec = recall([], when=dated({"1": datetime(2026, 10, 6), "2": datetime(2026, 10, 8)}))
    block = rec.as_context_block([hit(1, 0.9, "older note."), hit(2, 0.8, "newer note.")])
    lines = block.splitlines()
    assert lines[0] == "RELEVANT THINGS I REMEMBER:"
    assert lines[1] == "- (Thu 8 Oct) newer note." and lines[2] == "- (Tue 6 Oct) older note."


def test_a_hit_with_no_known_date_is_still_shown_after_the_dated_ones():
    rec = recall([], when=dated({"2": datetime(2026, 10, 8)}))
    block = rec.as_context_block([hit(1, 0.9, "undated note."), hit(2, 0.8, "dated note.")])
    assert block.splitlines()[1:] == ["- (Thu 8 Oct) dated note.", "- undated note."]


def test_a_failing_date_lookup_does_not_lose_the_memory():
    def boom(_):
        raise RuntimeError("db locked")

    block = recall([], when=boom).as_context_block([hit(1, 0.9, "kept note.")])
    assert "- kept note." in block


def test_each_hit_and_the_whole_block_stay_within_their_budgets():
    long_text = "A sentence about something. " * 40
    rec = recall([], max_hit_chars=100, max_total_chars=230)
    block = rec.as_context_block([hit(i, 0.9 - i / 100, long_text) for i in range(4)])
    body = block.splitlines()[1:]
    assert all(len(line) <= 100 + 3 for line in body) and sum(len(line) for line in body) <= 230 and len(body) == 2


def test_the_budget_is_spent_on_the_best_matches_before_the_newest():
    days = {"1": datetime(2026, 10, 1), "2": datetime(2026, 10, 8)}
    rec = recall([], when=dated(days), max_hit_chars=100, max_total_chars=60)
    block = rec.as_context_block([hit(1, 0.9, "best match, an older day."), hit(2, 0.7, "weaker match, a newer day.")])
    assert "best match" in block and "weaker match" not in block      # only one fits, and it is the better one


def test_an_empty_hit_list_gives_an_empty_block():
    assert recall([]).as_context_block([]) == ""


# ---- follow-ups: "what did he say?" is searched with the question before it ------------------------------------------------------------

class Recall:
    enabled = True

    def __init__(self, answers, about_conversation=False):
        self.answers, self.queries, self.about_conversation = answers, [], about_conversation

    async def asks_about_conversation(self, query):
        return self.about_conversation

    async def recall(self, query):
        self.queries.append(query)
        return self.answers.pop(0) if self.answers else []

    def as_context_block(self, hits):
        return "BLOCK" if hits else ""


class Conv:
    def __init__(self, turns, recent=""):
        self.turns, self.recent = turns, recent

    def get_summaries_block(self, limit=None):
        return ""

    def recent_summaries_block(self):
        return self.recent


class Knowledge:
    def get_context(self):
        return ""


def responder(recall, turns, recent=""):
    return ResponderAgent(client=None, conversation=Conv(turns, recent), knowledge=Knowledge(), recall=recall)


def prepare(rec, turns, message, recent=""):
    ctx = AgentContext(user_message=message)
    run(responder(rec, turns, recent)._prepare_semantic_context(ctx))
    return ctx


def turn(role, text):
    return SimpleNamespace(role=role, content=text)


PREV = [turn("user", "which emails did I send to Priya"), turn("assistant", "Four emails.")]


def test_a_short_follow_up_that_finds_nothing_is_retried_with_the_previous_question():
    rec = Recall([[], [hit(1, 0.7)]])
    ctx = prepare(rec, PREV, "what did she say")
    assert rec.queries == ["what did she say", "which emails did I send to Priya what did she say"]
    assert ctx.metadata["semantic_knowledge_context"] == "BLOCK"


def test_a_follow_up_that_already_matched_is_not_widened():
    rec = Recall([[hit(1, 0.7)]])
    prepare(rec, PREV, "what did she say")
    assert rec.queries == ["what did she say"]


def test_a_long_question_is_not_retried_with_the_previous_topic():
    rec = Recall([[]])
    ctx = prepare(rec, PREV, "tell me something completely different about the history of the Roman empire please")
    assert len(rec.queries) == 1 and "semantic_knowledge_context" not in ctx.metadata


def test_a_short_question_with_no_previous_turn_is_not_retried():
    rec = Recall([[]])
    prepare(rec, [], "what did she say")
    assert len(rec.queries) == 1


# ---- "what are we discussing?" is answered from the newest conversations ---------------------------------------------------------------

def test_a_question_about_the_conversation_uses_the_newest_conversations_not_similarity_search():
    rec = Recall([[hit(1, 0.9)]], about_conversation=True)
    ctx = prepare(rec, PREV, "what are we discussing", recent="RECENT CONVERSATIONS (newest first):\n- (Thu 8 Oct) note")
    assert ctx.metadata["semantic_knowledge_context"].startswith("RECENT CONVERSATIONS")
    assert rec.queries == []                                   # no similarity search was run


def test_with_no_earlier_conversations_it_falls_back_to_the_normal_search():
    rec = Recall([[hit(1, 0.7)]], about_conversation=True)
    ctx = prepare(rec, PREV, "what are we discussing", recent="")
    assert rec.queries == ["what are we discussing"] and ctx.metadata["semantic_knowledge_context"] == "BLOCK"


def test_an_ordinary_question_never_gets_the_recent_conversations_block():
    rec = Recall([[]], about_conversation=False)
    ctx = prepare(rec, PREV, "what is two plus two", recent="RECENT CONVERSATIONS (newest first):\n- (Thu 8 Oct) note")
    assert "semantic_knowledge_context" not in ctx.metadata


def test_the_intent_check_is_off_without_a_threshold_or_an_embedding_model():
    assert run(recall([]).asks_about_conversation("what are we discussing")) is False                  # no threshold
    assert run(SemanticRecall(Store([]), None, conversation_intent_min=0.8).asks_about_conversation("what are we discussing")) is False


def test_the_intent_check_compares_by_meaning_to_the_example_phrasings():
    class Vectors:
        model_id = "m"
        table = {"what are we discussing": (1.0, 0.0), "what have we been talking about": (0.9, 0.436)}

        async def embed(self, text):
            return SimpleNamespace(vector=self.table.get(text, (0.0, 1.0)))

    rec = SemanticRecall(Store([]), Vectors(), conversation_intent_min=0.9,
                         conversation_prototypes=("what are we discussing",))
    Vectors.table["recap please"] = (0.95, 0.312)
    assert run(rec.asks_about_conversation("recap please")) is True            # cosine 0.95
    assert run(rec.asks_about_conversation("something unrelated")) is False    # cosine 0.0
    assert abs(run(rec.conversation_intent_score("recap please")) - 0.95) < 1e-6 and run(rec.conversation_intent_score("")) == 0.0


# ---- the same on the real embedding model (skipped when the model is not staged) ---------------------------------------------------------

import pathlib

import pytest

_MODEL_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "bge-small-en-v1.5"

VAGUE = [
    "what are we talking about", "can you recap our conversation", "what's been going on in our chats", "what did we discuss yesterday",
    "remind me what we talked about before", "what were we talking about last time", "give me a summary of our previous chats",
    "what topics did we cover recently", "what was our last conversation about", "what are we discussing, till now?",
    "what were we on about earlier", "tell me what we spoke about", "what did we chat about today",
]
SPECIFIC = [
    "what did we talk about regarding Barcelona", "who founded Infosys, I asked you earlier", "which emails did I send to Manoj today",
    "what did Manoj reply about the invite", "did I ask about weather in Hyderabad", "what did we say about Barcelona",
    "what did he say in his reply", "what was the subject of the mail I sent", "did we talk about cricket",
]
ORDINARY = [
    "what is 2 plus 2", "tell me a joke", "good morning", "what is the capital of France", "how do I boil an egg",
    "what is the weather like in Pune", "set a reminder to call mom", "tell me about yourself", "how are you doing",
    "what are we having for dinner", "what is this song about", "what can you do",
]


@pytest.mark.skipif(not (_MODEL_DIR / "tokenizer.json").exists(), reason="embedding model not staged in data/")
def test_real_embedding_separates_questions_about_the_conversation_from_specific_and_ordinary_ones():
    from tpa.inference.embedding_adapter import OnnxEmbeddingProvider

    rec = SemanticRecall(Store([]), OnnxEmbeddingProvider(_MODEL_DIR), conversation_intent_min=0.82)
    caught = [q for q in VAGUE if run(rec.asks_about_conversation(q))]
    assert len(caught) >= len(VAGUE) - 1, f"missed: {set(VAGUE) - set(caught)}"
    wrong = [q for q in SPECIFIC + ORDINARY if run(rec.asks_about_conversation(q))]
    assert wrong == [], f"treated as 'about the conversation': {wrong}"


def test_shipped_config_enables_the_mode_and_keeps_the_margin_under_every_profile():
    from core.config import load_full_config

    cfg = load_full_config().embedding
    assert cfg.conversation_intent_min == 0.82 and cfg.margin == 0.05 and cfg.min_score == 0.62


def test_chat_uses_the_newest_conversations_when_the_orchestrator_already_decided_the_question_is_about_the_conversation():
    rec = Recall([[hit(1, 0.9)]], about_conversation=False)                   # its own check says no (a typo scored too low)
    ctx = AgentContext(user_message="can you tell me what are we discusing lately")
    ctx.metadata["about_conversation"] = True
    run(responder(rec, PREV, recent="RECENT CONVERSATIONS (newest first):\n- (Thu 8 Oct) note")._prepare_semantic_context(ctx))
    assert ctx.metadata["semantic_knowledge_context"].startswith("RECENT CONVERSATIONS") and rec.queries == []
