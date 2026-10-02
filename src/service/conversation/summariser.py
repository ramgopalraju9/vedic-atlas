"""ConversationSummariser — background compaction of old conversation turns.

Donor: veda/brain/summarizer.py's ConversationSummarizer, read in full and
ported closely:
  - The system prompt hardcoded a specific user's name ("Praveen"). This
    product is not built for one named individual — genericised to "the
    user" throughout.
  - Depends on ConversationRepositoryPort and InferencePort rather than
    concrete `ConversationRepository`/`LLMClient` types.
  - Runs on the `haiku` alias by default — per ADR-008 this now resolves
    to the SAME single local model as `sonnet` (there is one model, not
    two — see docs/roadmap/adr/ADR-008-model-class.md). Kept as a named
    parameter so that decision can change without touching this file.
  - Trigger rules (idle timeout OR batch threshold), the cold-tier GC that
    skips itself while any session has failed summaries, and the 3-retry
    give-up rule are all kept verbatim — these are real, sound design
    decisions, not incidental complexity.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from domain.ports.conversation_repository_port import ConversationRepositoryPort
from domain.ports.inference_port import InferencePort
from domain.entities.conversation import ConversationSummary
from core.logging_config import logger

SUMMARISER_SYSTEM = (
    "You condense conversations between the user and Veda (their AI assistant) "
    "into compact memos. Preserve: user preferences, decisions made and why, "
    "action items and outcomes, technical facts (project names, file paths, "
    "tool versions, issue numbers), and unresolved questions. Drop: greetings, "
    "small talk, error retry loops, pleasantries. Output one paragraph, max "
    "600 characters, third-person past tense (\"User asked X; Veda did Y; "
    "decision was Z\"). No markdown, no lists, no preamble."
)


class ConversationSummariser:
    def __init__(
        self,
        repo: ConversationRepositoryPort,
        client: InferencePort,
        *,
        idle_gap_min: int = 10,
        batch_threshold: int = 50,
        tick_sec: float = 60.0,
        summary_model: str | None = None,
        summary_num_predict: int = 256,
        cold_retention_days: int = 30,
        gc_interval_sec: float = 3600.0,
        summary_timeout_sec: int = 180,
        on_summary=None,
    ):
        self.repo = repo
        self.client = client
        self._on_summary = on_summary
        self.idle_gap_min = idle_gap_min
        self.batch_threshold = batch_threshold
        self.tick_sec = tick_sec
        self.summary_model = summary_model
        self.summary_num_predict = summary_num_predict
        self.cold_retention_days = cold_retention_days
        self.gc_interval_sec = gc_interval_sec
        self.summary_timeout_sec = summary_timeout_sec
        self._task: asyncio.Task | None = None
        self._gc_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._failure_counts: dict[str, int] = {}
        self._max_retries = 3

    # ---- Lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="conv-summariser")
        self._gc_task = asyncio.create_task(self._run_gc(), name="conv-summariser-gc")
        logger.info("[summariser] started")

    async def stop(self) -> None:
        self._stop.set()
        for t in (self._task, self._gc_task):
            if t is None:
                continue
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._gc_task = None
        logger.info("[summariser] stopped")

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await self.summarize_once()
                except Exception as e:
                    logger.warning(f"[summariser] tick failed: {e}")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.tick_sec)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass

    async def _run_gc(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.gc_interval_sec)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    self.run_gc_once()
                except Exception as e:
                    logger.warning(f"[summariser] gc failed: {e}")
        except asyncio.CancelledError:
            pass

    # ---- Core logic -----------------------------------------------------

    async def summarize_once(self, *, now: datetime | None = None) -> int | None:
        """Summarise the oldest eligible session. Returns the new summary id, or None."""
        now = now or datetime.now()
        eligible = self._pick_eligible_session(now=now)
        if eligible is None:
            return None
        session_id, _count, from_ts, to_ts = eligible
        turns = self.repo.un_summarized_by_session(session_id)
        if not turns:
            return None

        prompt = self._build_prompt(turns)
        try:
            text = await self.client.complete(
                prompt=prompt,
                system=SUMMARISER_SYSTEM,
                model=self.summary_model,
                timeout=self.summary_timeout_sec,
                num_predict=self.summary_num_predict,
            )
        except Exception as e:
            logger.warning(f"[summariser] inference call failed for session={session_id}: {e}")
            self._record_failure(session_id)
            return None

        text = (text or "").strip()
        if not text:
            self._record_failure(session_id)
            logger.info(f"[summariser] empty summary for session={session_id}; skipped")
            return None

        self._failure_counts.pop(session_id, None)

        summary_id = self.repo.add_summary(
            ConversationSummary(
                id=None,
                session_id=session_id,
                from_ts=from_ts,
                to_ts=to_ts,
                content=text,
                turn_count=len(turns),
                created_at=datetime.now(),
            )
        )
        self.repo.mark_summarized([t.id for t in turns])
        if self._on_summary is not None:
            try:
                await self._on_summary(summary_id, text)
            except Exception as e:
                logger.warning(f"[summariser] on_summary hook failed: {e}")
        logger.info(
            f"[summariser] session={session_id} summarized turns={len(turns)} -> summary_id={summary_id}"
        )
        return summary_id

    def run_gc_once(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now()
        if self._failure_counts:
            logger.debug("[summariser] gc skipped — sessions with failed summaries exist")
            return 0
        cutoff = now - timedelta(days=self.cold_retention_days)
        removed = self.repo.delete_summarized_before(cutoff)
        if removed:
            logger.info(f"[summariser] gc removed {removed} summarized turn(s)")
        return removed

    # ---- Internals ------------------------------------------------------

    def _pick_eligible_session(
        self, *, now: datetime
    ) -> tuple[str, int, datetime, datetime] | None:
        idle_cutoff = now - timedelta(minutes=self.idle_gap_min)
        for session_id, count, from_ts, to_ts in self.repo.sessions_with_unsummarized():
            if self._failure_counts.get(session_id, 0) >= self._max_retries:
                continue  # exhausted retries — skip until restart
            if to_ts <= idle_cutoff:
                return session_id, count, from_ts, to_ts
            if count >= self.batch_threshold:
                return session_id, count, from_ts, to_ts
        return None

    def _record_failure(self, session_id: str) -> None:
        count = self._failure_counts.get(session_id, 0) + 1
        self._failure_counts[session_id] = count
        if count >= self._max_retries:
            logger.warning(
                f"[summariser] session={session_id} failed {count} times; giving up until restart"
            )

    @staticmethod
    def _build_prompt(turns) -> str:
        lines = ["CONVERSATION:"]
        for t in turns:
            prefix = "User" if t.role == "user" else "Veda"
            lines.append(f"{prefix}: {t.content}")
        lines.append("")
        lines.append("Write the compact memo now.")
        return "\n".join(lines)


__all__ = ["ConversationSummariser", "SUMMARISER_SYSTEM"]