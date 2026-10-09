"""Per-exchange memory: the note, the vector store search, finding every pair, the hook on saving, per-source floors, and not
repeating what the chat prompt already shows. In-memory SQLite and fakes; nothing touches the real database."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.entities.conversation import Turn
from domain.policies.exchange_note_policy import exchange_note
from domain.value_objects.memory_hit import MemoryHit
from service.agent.responder import ResponderAgent
from service.conversation.conversation_manager import ConversationManager
from service.memory.semantic_recall import SemanticRecall
from tpa.persistence.models import conversation, memory_vector  # noqa: F401 (register tables)
from tpa.persistence.repositories.conversation_repository import ConversationRepository
from tpa.persistence.session import Base
from tpa.persistence.vector_store import SqliteVectorStore


def run(coro):
    return asyncio.run(coro)


def factory():
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)


# ---- the note -----------------------------------------------------------------------------------------------------------------

def test_a_note_is_the_words_that_were_said_not_a_paraphrase():
    assert exchange_note("which emails did I send", "Four sent emails to Manoj.") == "You: which emails did I send | Veda: Four sent emails to Manoj."


def test_long_text_is_clipped_at_a_word_and_whitespace_is_flattened():
    note = exchange_note("tell   me\nabout " + "something " * 40, "word " * 100)
    user, reply = note.removeprefix("You: ").split(" | Veda: ")
    assert len(user) <= 110 and user.endswith("…") and len(reply) <= 170 and reply.endswith("…") and "\n" not in note
    assert not user[:-1].endswith("someth")      # cut at a word, not inside one


def test_exchanges_with_nothing_to_recall_make_no_note():
    for user, reply in (("ok", "Sure thing."), ("what is the plan", ""), ("", "Hello there"), (None, None), ("hi", "Hello!")):
        assert exchange_note(user, reply) == ""


# ---- the vector store ---------------------------------------------------------------------------------------------------------------

def vec(*xs):
    return tuple(float(x) for x in xs)


def test_search_ranks_by_cosine_and_respects_source_model_and_top_k():
    store = SqliteVectorStore(session_factory=factory())
    store.upsert(source="exchange", ref_id="1", model_id="m", vector=vec(1, 0, 0), text="a")
    store.upsert(source="exchange", ref_id="2", model_id="m", vector=vec(0.8, 0.6, 0), text="b")
    store.upsert(source="summary", ref_id="3", model_id="m", vector=vec(0, 1, 0), text="c")
    store.upsert(source="exchange", ref_id="4", model_id="other", vector=vec(1, 0, 0), text="d")
    got = store.search(vector=vec(1, 0, 0), model_id="m", top_k=5)
    assert [(h.ref_id, round(h.score, 3)) for h in got] == [("1", 1.0), ("2", 0.8), ("3", 0.0)]
    assert [h.ref_id for h in store.search(vector=vec(1, 0, 0), model_id="m", top_k=1)] == ["1"]
    assert [h.ref_id for h in store.search(vector=vec(1, 0, 0), model_id="m", sources=("summary",))] == ["3"]
    assert store.search(vector=vec(1, 0, 0), model_id="nobody") == []


def test_search_ignores_a_zero_query_and_rows_of_another_dimension():
    store = SqliteVectorStore(session_factory=factory())
    store.upsert(source="exchange", ref_id="1", model_id="m", vector=vec(1, 0, 0), text="a")
    store.upsert(source="exchange", ref_id="2", model_id="m", vector=vec(1, 0), text="wrong size")
    assert store.search(vector=vec(0, 0, 0), model_id="m") == []
    assert [h.ref_id for h in store.search(vector=vec(1, 0, 0), model_id="m")] == ["1"]


def test_ref_ids_and_delete_refs():
    store = SqliteVectorStore(session_factory=factory())
    for i in range(1, 6):
        store.upsert(source="exchange", ref_id=str(i), model_id="m", vector=vec(1, 0), text="t")
    store.upsert(source="summary", ref_id="1", model_id="m", vector=vec(1, 0), text="t")
    assert store.ref_ids(source="exchange", model_id="m") == {"1", "2", "3", "4", "5"}
    assert store.delete_refs(source="exchange", ref_ids={"2", "4", "99"}) == 2
    assert store.ref_ids(source="exchange", model_id="m") == {"1", "3", "5"}
    assert store.ref_ids(source="summary", model_id="m") == {"1"}                 # another source is untouched
    assert store.delete_refs(source="exchange", ref_ids=set()) == 0


# ---- finding every question-answer pair -------------------------------------------------------------------------------------------------

def add(r, session, role, text, when):
    return r.add_turn(Turn(id=None, session_id=session, role=role, content=text, created_at=when))


def test_exchanges_pair_each_question_with_its_answer_and_skip_unprompted_messages():
    r, t0 = ConversationRepository(session_factory=factory()), datetime(2026, 10, 9, 10, 0)
    add(r, "s", "user", "first question", t0)
    a1 = add(r, "s", "assistant", "first answer", t0 + timedelta(seconds=5))
    add(r, "s", "assistant", "a reminder nobody asked for", t0 + timedelta(minutes=1))
    add(r, "s", "user", "second question", t0 + timedelta(minutes=2))
    a2 = add(r, "s", "assistant", "second answer", t0 + timedelta(minutes=2, seconds=5))
    add(r, "other", "user", "unanswered question", t0 + timedelta(minutes=3))
    pairs = r.exchanges()
    assert [(p[0], p[2], p[3]) for p in pairs] == [(a1, "first question", "first answer"), (a2, "second question", "second answer")]
    assert r.turn_created_at(a1) == t0 + timedelta(seconds=5) and r.turn_created_at(9999) is None


# ---- the hook when a question and its answer are saved ----------------------------------------------------------------------------------

def manager(**kw):
    seen = []
    mgr = ConversationManager(ConversationRepository(session_factory=factory()), on_exchange=lambda i, note: seen.append((i, note)), **kw)
    return mgr, seen


def test_saving_a_question_and_its_answer_reports_one_exchange_with_the_answer_turn_id():
    mgr, seen = manager()
    mgr.add_turn("user", "what time is the team sync", session_id="s")
    answer_id = mgr.add_turn("assistant", "The team sync is at 10 PM.", session_id="s")
    assert seen == [(answer_id, "You: what time is the team sync | Veda: The team sync is at 10 PM.")]


def test_an_answer_with_no_question_or_a_trivial_exchange_is_not_reported():
    mgr, seen = manager()
    mgr.add_turn("assistant", "Reminder: standup is starting now.", session_id="s")      # unprompted
    mgr.add_turn("user", "ok", session_id="s")
    mgr.add_turn("assistant", "Sure thing, glad to help.", session_id="s")
    assert seen == []


def test_exchanges_of_different_sessions_do_not_get_mixed_up():
    mgr, seen = manager()
    mgr.add_turn("user", "question in session one", session_id="one")
    mgr.add_turn("user", "question in session two", session_id="two")
    mgr.add_turn("assistant", "answer for session two", session_id="two")
    mgr.add_turn("assistant", "answer for session one", session_id="one")
    assert [n for _, n in seen] == ["You: question in session two | Veda: answer for session two",
                                    "You: question in session one | Veda: answer for session one"]


def test_a_failing_index_hook_never_loses_the_turn():
    def boom(i, note):
        raise RuntimeError("index down")

    mgr = ConversationManager(ConversationRepository(session_factory=factory()), on_exchange=boom)
    mgr.add_turn("user", "a perfectly good question", session_id="s")
    assert mgr.add_turn("assistant", "a perfectly good answer", session_id="s") is not None
    assert [t.content for t in mgr.turns_in("s")] == ["a perfectly good question", "a perfectly good answer"]


# ---- per-source floors -------------------------------------------------------------------------------------------------------------------

class Embedding:
    model_id = "m"

    async def embed(self, text):
        return SimpleNamespace(vector=(1.0,))


class Fixed:
    def __init__(self, hits):
        self.hits = hits

    def search(self, *, vector, model_id, top_k, sources):
        pool = [h for h in self.hits if not sources or h.source in sources]
        return sorted(pool, key=lambda h: h.score, reverse=True)[:top_k]


def hit(source, ref, score):
    return MemoryHit(source=source, ref_id=str(ref), text="t", score=score)


def test_exchanges_need_a_higher_score_than_summaries():
    rec = SemanticRecall(Fixed([hit("exchange", 1, 0.66), hit("summary", 2, 0.64), hit("exchange", 3, 0.72)]), Embedding(),
                         top_k=4, min_score=0.62, margin=None, sources=("summary", "exchange"), min_score_by_source={"exchange": 0.70})
    assert [(h.source, h.ref_id) for h in run(rec.recall("q"))] == [("exchange", "3"), ("summary", "2")]


def test_a_strict_source_does_not_crowd_the_others_out_of_the_top_few():
    many_exchanges = [hit("exchange", i, 0.69 - i / 1000) for i in range(10)]            # all below the exchange floor
    rec = SemanticRecall(Fixed(many_exchanges + [hit("summary", 99, 0.64)]), Embedding(), top_k=2, min_score=0.62, margin=None,
                         sources=("summary", "exchange"), min_score_by_source={"exchange": 0.70})
    assert [h.ref_id for h in run(rec.recall("q"))] == ["99"]


def test_without_per_source_floors_nothing_changes():
    rec = SemanticRecall(Fixed([hit("exchange", 1, 0.66), hit("summary", 2, 0.64)]), Embedding(), top_k=4, min_score=0.62, margin=None,
                         sources=("summary", "exchange"))
    assert len(run(rec.recall("q"))) == 2


# ---- chat does not repeat what is already in the history above it -------------------------------------------------------------------------

class Recall:
    enabled = True

    def __init__(self, hits):
        self.hits = hits

    async def asks_about_conversation(self, query):
        return False

    async def recall(self, query):
        return self.hits

    def as_context_block(self, hits):
        return ",".join(f"{h.source}:{h.ref_id}" for h in hits)


class Conv:
    def __init__(self, turns):
        self.turns = turns

    def get_summaries_block(self, limit=None):
        return ""


class Knowledge:
    def get_context(self):
        return ""


def test_an_exchange_already_shown_in_the_recent_history_is_not_recalled_again():
    shown_turns = [SimpleNamespace(id=41, role="user", content="q"), SimpleNamespace(id=42, role="assistant", content="a")]
    recall = Recall([hit("exchange", 42, 0.9), hit("exchange", 7, 0.8), hit("summary", 42, 0.75)])
    agent = ResponderAgent(client=None, conversation=Conv(shown_turns), knowledge=Knowledge(), recall=recall)
    ctx = AgentContext(user_message="what was that about")
    run(agent._prepare_semantic_context(ctx))
    assert ctx.metadata["semantic_knowledge_context"] == "exchange:7,summary:42"        # 42 as an exchange is in the history; as a summary it is not


def test_shipped_config_searches_exchanges_with_a_stricter_floor():
    from core.config import load_full_config

    cfg = load_full_config().embedding
    assert "exchange" in cfg.sources and cfg.min_score_by_source == {"exchange": 0.70}
