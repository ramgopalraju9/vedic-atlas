"""SummaryBackfill — write the missing summaries for conversations that were summarised away without leaving one.

Why it exists: turns are flagged `summarized` when the summariser folds them into a summary. If the summary rows are later
lost (a table wipe, a restore from an older backup) the turns stay flagged, so they are in neither the history nor any
summary and can never be recalled; the 30-day cleanup then deletes them for good. The raw text is still in the database
until then, so this rebuilds the summaries from it.

Differences from the live summariser, chosen for recall: one summary per ~12 turns (a 50-turn session as one paragraph
loses its details), the conversation's DATE is in the input and relative words are forbidden (a summary read a week later
saying "tomorrow" is wrong), and the length cap is enforced in code, not only requested. Each summary is dated by the
conversation (created_at = its last turn) so the "earlier sessions" block shows the right day.

Safe to run twice: a session is only touched while it has no summary at all, a session is written all-or-nothing, and a
session that is still live or has turns waiting for the normal summariser is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Sequence

from core.logging_config import logger
from domain.entities.conversation import ConversationSummary, Turn
from domain.policies.session_boundary_policy import DEFAULT_SESSION_GAP_MIN
from domain.ports.conversation_repository_port import ConversationRepositoryPort
from domain.ports.inference_port import InferencePort

BACKFILL_SYSTEM = (
    "You write short memory notes about one stretch of a conversation between the user and Veda (their voice assistant). "
    "Keep the concrete details a later question could need: what was asked, what Veda answered or did, names, email "
    "subjects, times, numbers, decisions. Use the date you are given and never write today, tomorrow or yesterday. If Veda "
    "said it could not do something or reported an error, say exactly that. Output one paragraph of at most 350 "
    "characters, third person, past tense, no markdown, no preamble."
)
_LINE_CHARS = 400


def chunk_turns(turns: Sequence[Turn], size: int = 12) -> list[list[Turn]]:
    """Consecutive groups of about `size` turns; a group never starts with a reply (it stays with its question)."""
    groups: list[list[Turn]] = []
    for turn in turns:
        if not groups or (len(groups[-1]) >= size and turn.role == "user"):
            groups.append([])
        groups[-1].append(turn)
    return groups


def cap_summary(text: str, max_chars: int = 450) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= max_chars // 2:
        return cut[: end + 1]
    return cut[: cut.rfind(" ")].rstrip(",;: ") + "…"


def build_prompt(turns: Sequence[Turn]) -> str:
    first = turns[0].created_at
    lines = [f"CONVERSATION on {first.strftime('%A %d %B %Y')}, from {first.strftime('%H:%M')}:"]
    for t in turns:
        who = "User" if t.role == "user" else "Veda"
        lines.append(f"{who}: {' '.join((t.content or '').split())[:_LINE_CHARS]}")
    lines.append("")
    lines.append("Write the memory note now.")
    return "\n".join(lines)


@dataclass
class BackfillReport:
    sessions_planned: int = 0
    chunks_planned: int = 0
    sessions_written: int = 0
    summaries_written: int = 0
    sessions_failed: list[str] = field(default_factory=list)


class SummaryBackfill:
    def __init__(
        self,
        repo: ConversationRepositoryPort,
        client: InferencePort,
        *,
        indexer: Callable[[int, str], Awaitable[object]] | None = None,
        model: str | None = None,
        num_predict: int = 160,
        timeout_sec: int = 180,
        chunk_size: int = 12,
        max_chars: int = 450,
        live_gap_min: int = DEFAULT_SESSION_GAP_MIN,
        now: Callable[[], datetime] = datetime.now,
    ):
        self._repo = repo
        self._client = client
        self._indexer = indexer
        self._model = model
        self._num_predict = num_predict
        self._timeout = timeout_sec
        self._chunk = chunk_size
        self._max_chars = max_chars
        self._live_gap = live_gap_min
        self._now = now

    def plan(self) -> list[tuple[str, list[list[Turn]]]]:
        """(session_id, chunks) for every finished session that has turns but no summary and nothing pending."""
        pending = {sid for sid, *_ in self._repo.sessions_with_unsummarized()}
        over_before = self._now() - timedelta(minutes=self._live_gap)
        planned = []
        for session_id, _count, _from_ts, to_ts in self._repo.sessions_without_summary():
            if session_id in pending or to_ts > over_before:
                continue
            turns = self._repo.turns_by_session(session_id)
            if turns:
                planned.append((session_id, chunk_turns(turns, self._chunk)))
        return planned

    async def run(self, *, apply: bool) -> BackfillReport:
        plan = self.plan()
        report = BackfillReport(sessions_planned=len(plan), chunks_planned=sum(len(c) for _, c in plan))
        if not apply:
            return report
        for session_id, chunks in plan:
            notes: list[tuple[list[Turn], str]] = []
            for chunk in chunks:
                try:
                    text = await self._client.complete(
                        prompt=build_prompt(chunk), system=BACKFILL_SYSTEM, model=self._model,
                        timeout=self._timeout, num_predict=self._num_predict,
                    )
                except Exception as e:
                    logger.warning(f"[backfill] model call failed for session {session_id}: {type(e).__name__}")
                    break
                text = cap_summary(text, self._max_chars)
                if not text:
                    break
                notes.append((chunk, text))
            if len(notes) != len(chunks):   # all-or-nothing: a half-written session would never be retried
                report.sessions_failed.append(session_id)
                continue
            for chunk, text in notes:
                summary_id = self._repo.add_summary(ConversationSummary(
                    id=None, session_id=session_id, from_ts=chunk[0].created_at, to_ts=chunk[-1].created_at,
                    content=text, turn_count=len(chunk), created_at=chunk[-1].created_at,
                ))
                if self._indexer is not None:
                    await self._indexer(summary_id, text)
                report.summaries_written += 1
            report.sessions_written += 1
        return report
