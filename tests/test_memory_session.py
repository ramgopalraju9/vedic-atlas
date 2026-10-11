"""Memory fixes: the live session keeps its history, the summariser waits for the conversation to be over, and the missing
summaries can be rebuilt. In-memory SQLite and a fake model; nothing here touches the real database."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.entities.conversation import ConversationSummary, Turn
from service.conversation.backfill import SummaryBackfill, build_prompt, cap_summary, chunk_turns
from service.conversation.conversation_manager import ConversationManager
from service.conversation.summariser import ConversationSummariser
from tpa.persistence.models import conversation  # noqa: F401 (register tables)
from tpa.persistence.repositories.conversation_repository import ConversationRepository
from tpa.persistence.session import Base

NOW = datetime(2026, 10, 9, 12, 0)


def run(coro):
    return asyncio.run(coro)


def repo():
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return ConversationRepository(session_factory=sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False))


def turn(session, role, text, when, summarized=False):
    return Turn(id=None, session_id=session, role=role, content=text, created_at=when, summarized=summarized)


def add_pairs(r, session, n, start, summarized=False, step=timedelta(minutes=1)):
    for i in range(n):
        r.add_turn(turn(session, "user", f"question {i}", start + step * (2 * i), summarized))
        r.add_turn(turn(session, "assistant", f"answer {i}", start + step * (2 * i + 1), summarized))


class Model:
    def __init__(self, reply="They talked about plans.", fail_on=None):
        self.reply, self.fail_on, self.prompts = reply, fail_on, []

    async def complete(self, prompt, system="", model=None, timeout=None, *, num_predict=None, **_):
        self.prompts.append(prompt)
        if self.fail_on is not None and len(self.prompts) == self.fail_on:
            raise RuntimeError("model down")
        return self.reply


# ---- the live session keeps its history --------------------------------------------------------------------------------------

def test_turns_the_summariser_already_folded_stay_visible_while_the_session_is_live():
    r = repo()
    now = datetime.now()
    add_pairs(r, "live", 3, now - timedelta(minutes=10), summarized=True)
    r.add_turn(turn("live", "user", "newest question", now - timedelta(minutes=1)))
    shown = [t.content for t in ConversationManager(r).turns]
    assert shown[0] == "question 0" and shown[-1] == "newest question" and len(shown) == 7


def test_the_live_sessions_own_summary_is_not_shown_beside_its_raw_turns():
    r = repo()
    now = datetime.now()
    add_pairs(r, "live", 2, now - timedelta(minutes=5))
    r.add_summary(ConversationSummary(None, "old", now - timedelta(days=1), now - timedelta(days=1), "yesterday note", 4, now - timedelta(days=1)))
    r.add_summary(ConversationSummary(None, "live", now - timedelta(minutes=5), now, "live session note", 4, now))
    block = ConversationManager(r, summaries_limit=3).get_summaries_block()
    assert "yesterday note" in block and "live session note" not in block


def test_with_summaries_disabled_nothing_is_added():
    assert ConversationManager(repo(), summaries_limit=0).get_summaries_block() == ""


# ---- the summariser waits for the conversation to be over ----------------------------------------------------------------------

def picks(r, now, **kw):
    return ConversationSummariser(r, client=None, **kw)._pick_eligible_session(now=now)   # type: ignore[arg-type]


def test_a_session_is_not_summarised_while_it_could_still_continue():
    r = repo()
    add_pairs(r, "s", 3, NOW - timedelta(minutes=20))                       # last turn 15 minutes ago, 6 turns
    assert picks(r, NOW) is None                                            # the old 10-minute rule would have taken it


def test_a_session_is_summarised_once_the_session_gap_has_passed():
    r = repo()
    add_pairs(r, "s", 3, NOW - timedelta(minutes=60))                       # last turn 55 minutes ago
    assert picks(r, NOW)[0] == "s"


def test_a_long_session_is_compacted_early_only_when_the_user_has_paused():
    r = repo()
    add_pairs(r, "busy", 30, NOW - timedelta(minutes=30), step=timedelta(seconds=30))    # 60 turns, the last one 30 s ago
    assert picks(r, NOW) is None                                                         # mid-conversation: leave it alone
    assert picks(r, NOW + timedelta(minutes=5))[0] == "busy"                             # paused for 5 minutes: allowed
    assert picks(r, NOW + timedelta(minutes=1)) is None                                  # paused for only 1.5 minutes: not yet


# ---- summaries are dated by the conversation ---------------------------------------------------------------------------------------

def test_add_summary_honours_the_given_date():
    r = repo()
    when = datetime(2026, 10, 6, 12, 30)
    sid = r.add_summary(ConversationSummary(None, "s", when, when, "note", 2, when))
    assert [s.created_at for s in r.recent_summaries(limit=1)] == [when] and sid


# ---- backfill ----------------------------------------------------------------------------------------------------------------------------

def test_chunks_keep_a_question_with_its_answer():
    r = repo()
    add_pairs(r, "s", 13, NOW - timedelta(days=3))
    chunks = chunk_turns(r.turns_by_session("s"), size=12)
    assert [len(c) for c in chunks] == [12, 12, 2] and all(c[0].role == "user" for c in chunks)


def test_the_cap_is_enforced_at_a_sentence_end_when_possible():
    text = "First thing happened. " * 40
    out = cap_summary(text, 100)
    assert len(out) <= 101 and out.endswith(".")
    assert cap_summary("short", 100) == "short" and cap_summary("", 100) == ""
    assert len(cap_summary("word " * 300, 80)) <= 81


def test_the_prompt_carries_the_date_and_both_sides():
    r = repo()
    add_pairs(r, "s", 1, datetime(2026, 10, 6, 12, 15))
    p = build_prompt(r.turns_by_session("s"))
    assert "Tuesday 06 October 2026" in p and "User: question 0" in p and "Veda: answer 0" in p


def lost_summaries_repo():
    r = repo()
    add_pairs(r, "lost", 14, NOW - timedelta(days=3), summarized=True)                     # 28 turns, flagged, no summary row
    add_pairs(r, "kept", 2, NOW - timedelta(days=2), summarized=True)
    r.add_summary(ConversationSummary(None, "kept", NOW, NOW, "kept note", 4, NOW))        # this one has its summary
    add_pairs(r, "pending", 2, NOW - timedelta(days=1))                                    # waiting for the normal summariser
    add_pairs(r, "live", 2, NOW - timedelta(minutes=10))                                   # still going on
    return r


def test_only_finished_sessions_without_any_summary_are_planned():
    plan = SummaryBackfill(lost_summaries_repo(), Model(), now=lambda: NOW).plan()
    assert [sid for sid, _ in plan] == ["lost"] and [len(c) for c in plan[0][1]] == [12, 12, 4]


def test_dry_run_writes_nothing_and_does_not_call_the_model():
    r, m = lost_summaries_repo(), Model()
    report = run(SummaryBackfill(r, m, now=lambda: NOW).run(apply=False))
    assert (report.sessions_planned, report.chunks_planned, report.summaries_written) == (1, 3, 0) and m.prompts == []
    assert len(r.recent_summaries(limit=10)) == 1


def test_apply_writes_dated_capped_indexed_summaries_and_is_safe_to_repeat():
    r, m, indexed = lost_summaries_repo(), Model("x " * 600), []

    async def index(sid, text):
        indexed.append((sid, len(text)))

    bf = SummaryBackfill(r, m, indexer=index, max_chars=450, now=lambda: NOW)
    report = run(bf.run(apply=True))
    assert (report.sessions_written, report.summaries_written, report.sessions_failed) == (1, 3, [])
    written = [s for s in r.recent_summaries(limit=10) if s.session_id == "lost"]
    assert len(written) == 3 and all(len(s.content) <= 451 for s in written) and len(indexed) == 3
    assert {s.created_at.date() for s in written} == {(NOW - timedelta(days=3)).date()}          # dated by the conversation
    again = run(SummaryBackfill(r, m, now=lambda: NOW).run(apply=True))
    assert again.sessions_planned == 0 and len(r.recent_summaries(limit=10)) == 4               # nothing duplicated


def test_a_session_is_all_or_nothing_so_a_failure_leaves_it_to_be_retried():
    r = lost_summaries_repo()
    report = run(SummaryBackfill(r, Model(fail_on=2), now=lambda: NOW).run(apply=True))
    assert report.sessions_failed == ["lost"] and report.summaries_written == 0
    assert len([s for s in r.recent_summaries(limit=10) if s.session_id == "lost"]) == 0
    retry = run(SummaryBackfill(r, Model(), now=lambda: NOW).run(apply=True))
    assert retry.summaries_written == 3


def test_an_empty_model_answer_counts_as_a_failure():
    r = lost_summaries_repo()
    assert run(SummaryBackfill(r, Model(reply="   "), now=lambda: NOW).run(apply=True)).sessions_failed == ["lost"]


# ---- recent_summaries_block: the newest earlier conversations ---------------------------------------------------------------------------

def three_days(r):
    for i, sid in enumerate(("a", "b", "c")):
        day = datetime(2026, 10, 3 + i, 12, 0)
        r.add_summary(ConversationSummary(None, sid, day, day, f"note {sid}.", 4, day))


def test_recent_summaries_block_is_newest_first_dated_and_limited():
    r = repo()
    three_days(r)
    block = ConversationManager(r, summaries_limit=0).recent_summaries_block(limit=2)      # works even with summaries switched off
    assert block.splitlines() == ["RECENT CONVERSATIONS (newest first):", "- (Mon 5 Oct) note c.", "- (Sun 4 Oct) note b."]


def test_recent_summaries_block_leaves_out_the_live_sessions_own_summary():
    r = repo()
    three_days(r)
    now = datetime.now()
    add_pairs(r, "live", 1, now - timedelta(minutes=3))
    r.add_summary(ConversationSummary(None, "live", now, now, "live note.", 2, now))
    block = ConversationManager(r).recent_summaries_block(limit=3)
    assert "live note" not in block and "note c." in block


def test_recent_summaries_block_is_capped_and_empty_without_history():
    r = repo()
    day = datetime(2026, 10, 3, 12, 0)
    for sid in ("a", "b", "c"):
        r.add_summary(ConversationSummary(None, sid, day, day, "A long sentence about nothing. " * 20, 4, day))
    capped = ConversationManager(r).recent_summaries_block(limit=3, max_hit_chars=80, max_total_chars=200)
    assert all(len(line) <= 100 for line in capped.splitlines()[1:]) and len(capped.splitlines()) - 1 == 2
    assert ConversationManager(repo()).recent_summaries_block() == ""
