"""Phase 4.3 / 4.4: chat history is bounded (per-turn clip + token budget) and scoped to the current session."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.agent_context import AgentContext
from domain.entities.conversation import Turn
from domain.policies.token_budget_policy import estimate_tokens
from service.agent.responder import ResponderAgent
from service.conversation.conversation_manager import ConversationManager
from tpa.persistence.models import conversation  # noqa: F401 (register tables)
from tpa.persistence.repositories.conversation_repository import ConversationRepository
from tpa.persistence.session import Base


def _repo():
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return ConversationRepository(session_factory=sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False))


def _turn(session_id, role, text, when):
    return Turn(id=None, session_id=session_id, role=role, content=text, created_at=when)


# ---- session scope ---------------------------------------------------------------------------------

def test_turns_are_scoped_to_the_current_session():
    repo, now = _repo(), datetime.now()
    repo.add_turn(_turn("old", "user", "an hour ago, other session", now - timedelta(hours=2)))
    repo.add_turn(_turn("cur", "user", "current question", now - timedelta(minutes=2)))
    repo.add_turn(_turn("cur", "assistant", "current answer", now - timedelta(minutes=1)))
    mgr = ConversationManager(repo)
    assert [t.content for t in mgr.turns] == ["current question", "current answer"]
    assert [t.content for t in mgr.turns_in("old")] == ["an hour ago, other session"]
    assert len(repo.recent_turns(limit=10)) == 3  # the repository default is still global


def test_a_new_session_starts_with_no_history():
    repo = _repo()
    repo.add_turn(_turn("old", "user", "stale", datetime.now() - timedelta(hours=3)))
    assert ConversationManager(repo).turns == []  # 30-minute gap -> a fresh session id with no turns


# ---- responder bound -------------------------------------------------------------------------------

class _Conv:
    def __init__(self, turns): self.turns = turns
    def get_summaries_block(self, limit=None): return ""


class _Knowledge:
    def get_context(self): return ""


def _responder(turns, budget):
    return ResponderAgent(client=None, conversation=_Conv(turns), knowledge=_Knowledge(),
                          chat_budget_tokens=budget, history_turn_chars=50)


def _turns(n, size=300):
    now = datetime.now()
    return [SimpleNamespace(role="user" if i % 2 == 0 else "assistant", content=f"turn{i} " + "x" * size, created_at=now)
            for i in range(n)]


def test_each_history_turn_is_clipped():
    prompt = _responder(_turns(2), None).build_prompt(AgentContext(user_message="hi"))
    line = next(l for l in prompt.splitlines() if l.startswith("User: turn0"))
    assert len(line) <= len("User: ") + 50 and line.endswith("…")


def test_history_is_trimmed_oldest_first_to_the_chat_budget():
    turns, ctx = _turns(12), AgentContext(user_message="hi")
    unbounded = _responder(turns, None).build_prompt(ctx)
    bare = _responder([], None)
    fixed = estimate_tokens(bare.system_prompt) + estimate_tokens(bare.build_prompt(ctx))
    budget = fixed + 80                                         # room for roughly four clipped turns
    r = _responder(turns, budget)
    bounded = r.build_prompt(ctx)
    assert estimate_tokens(r.system_prompt) + estimate_tokens(bounded) <= budget
    assert "turn11" in bounded and "turn0" not in bounded      # newest kept, oldest dropped
    assert len(bounded) < len(unbounded)


def test_a_budget_too_small_for_history_drops_it_but_keeps_the_question():
    prompt = _responder(_turns(6), 1).build_prompt(AgentContext(user_message="what is 2+2"))
    assert "RECENT CONVERSATION" not in prompt and "USER: what is 2+2" in prompt
