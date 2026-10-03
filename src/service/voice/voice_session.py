"""VoiceSession — the always-on listen -> think -> speak loop.

★ New, PS-mandatory (REQ-M-01). This is the orchestrator the migration
never built: every adapter it needs already existed in tpa/, but nothing
connected them. It owns no I/O itself — every capability arrives as a
Port, so the Pi profile swaps adapters (whisper.cpp, Piper, GPIO mute)
with zero changes here.

    mic ──frames──> CaptureGate (muted?) ──> VAD ──> UtteranceCollector
      └──> STT ──> SupervisorAgent ──> TTS ──> speaker

Three properties that matter more than the happy path:

  * **Mute is checked every single frame**, and a mute mid-sentence
    discards the in-progress utterance rather than transcribing it. The
    gate is consulted, never cached — see service/privacy/capture_gate.py.

  * **Echo suppression.** While Veda speaks, its own voice reaches the
    mic. Capture is drained and ignored for the duration of playback,
    otherwise the assistant transcribes itself and talks in a loop. This
    is the single most common way an always-on voice demo fails.

  * **`from_voice=True`** flows into AgentContext so the responder keeps
    replies to one or two sentences — a paragraph that reads fine on
    screen is unbearable spoken aloud.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Callable

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.utterance import Utterance
from domain.policies.transcript_policy import artifact_reason
from domain.ports.audio_capture_port import AudioCapturePort
from domain.ports.stt_port import STTPort
from domain.ports.tts_port import TTSPort
from service.voice.utterance_collector import UtteranceCollector

_WORD_RE = re.compile(r"[a-z0-9']+")
_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s)")


class VoiceSession:
    """Continuous listen/think/speak loop, gated by the mute switch."""

    def __init__(
        self,
        audio: AudioCapturePort,
        collector: UtteranceCollector,
        stt: STTPort,
        tts: TTSPort,
        speaker: Any,
        supervisor: Any,
        capture_gate: Any,
        *,
        min_chars: int = 2,
        speak_replies: bool = True,
        speak_timeout_sec: float = 60.0,
        post_speak_settle_sec: float = 0.4,
        echo_guard: bool = True,
        wake_word: Any | None = None,
        wake_engine: str = "none",
        wake_window_sec: float = 8.0,
        speaker_id: Any | None = None,
        require_known_speaker: bool = False,
        speaker_match_threshold: float = 0.6,
        on_event: Callable[[dict], None] | None = None,
    ):
        self._audio = audio
        self._collector = collector
        self._stt = stt
        self._tts = tts
        self._speaker = speaker
        self._supervisor = supervisor
        self._gate = capture_gate
        self._min_chars = min_chars
        self._speak_replies = speak_replies
        self._speak_timeout_sec = speak_timeout_sec
        self._post_speak_settle_sec = post_speak_settle_sec
        self._echo_guard = echo_guard
        self._on_event = on_event or (lambda _e: None)

        # Wake-word gating. When _wake is None the loop transcribes while
        # unmuted (original behaviour); otherwise it stays PASSIVE until the
        # trigger arms a listening window.
        self._wake = wake_word
        self._wake_engine = wake_engine
        self._wake_window_sec = wake_window_sec
        self._wake_frame_bytes = (getattr(wake_word, "frame_length", 0) or 0) * 2
        self._wake_buffer = b""
        self._armed_until = 0.0

        # Multi-speaker recognition. Scored continuously, frame by frame,
        # while armed/collecting (same slicing idiom as the wake buffer
        # above) - but the accept/reject decision is made once per
        # utterance, off the accumulated mean score, not frame by frame.
        # A single noisy frame scoring low must not clip real speech from
        # an enrolled speaker.
        self._speaker_id = speaker_id
        self._require_known_speaker = require_known_speaker
        self._speaker_match_threshold = speaker_match_threshold
        self._speaker_frame_bytes = (getattr(speaker_id, "frame_length", 0) or 0) * 2
        self._speaker_buffer = b""
        self._speaker_score_sums: list[float] = []
        self._speaker_score_count = 0

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._speaking = False
        self._turns = 0
        self._last_transcript = ""
        self._last_reply = ""

    # -- Lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._speaker.start()
        self._task = asyncio.create_task(self._run(), name="VoiceSession")
        logger.info("[voice] session started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._collector.reset()
        try:
            self._speaker.stop()
        except Exception:
            pass
        wake_stop = getattr(self._wake, "stop", None)
        if callable(wake_stop):
            try:
                wake_stop()
            except Exception:
                pass
        logger.info("[voice] session stopped")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return {
            "running": self.is_running,
            "muted": self._gate.is_muted(),
            "speaking": self._speaking,
            "listening": self.is_running and not self._gate.is_muted() and not self._speaking and (self._wake is None or self._is_armed()),
            "collecting_utterance": self._collector.is_speaking,
            "turns": self._turns,
            "last_transcript": self._last_transcript,
            "last_reply": self._last_reply,
            "wake_engine": self._wake_engine,
            "armed": self._is_armed(),
            "speaker_id_enabled": self._speaker_id is not None,
            "require_known_speaker": self._require_known_speaker,
        }

    # -- Loop -------------------------------------------------------------

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[voice] loop error: {e}")
                await asyncio.sleep(0.2)

    async def _tick(self) -> None:
        # Muted: never read the mic at all. Not "read and discard" — the
        # frames must not enter the process (REQ-M-04).
        if self._gate.is_muted():
            if self._collector.is_speaking:
                self._collector.reset()
            self._disarm(silent=True)
            await asyncio.sleep(0.15)
            return

        # While speaking, ignore the mic so we don't transcribe ourselves.
        if self._speaking:
            await asyncio.sleep(0.05)
            return

        frame = await asyncio.to_thread(self._audio.read, 0.5)
        if frame is None:
            return

        # Wake-word gate: while not armed, only listen for the trigger. When no
        # wake engine is configured (_wake is None) this block is skipped and
        # the loop transcribes continuously, exactly as before.
        if self._wake is not None and not self._is_armed():
            if self._wake_triggered(frame):
                self._arm()
            return

        if self._speaker_id is not None:
            self._feed_speaker_frame(frame)

        utterance = self._collector.push(frame)
        if utterance is not None:
            if not await self._run_turn(utterance):
                return  # muted mid-turn: it was cancelled and dropped; stay passive
            if self._wake is not None:
                # Extend the window so a quick follow-up needs no second trigger.
                self._arm()
        elif self._wake is not None and self._armed_expired():
            self._disarm()

    # -- Running a turn, with mute as an interrupt -------------------------------

    async def _run_turn(self, utterance: Utterance) -> bool:
        """Run one turn as a task and watch the mute switch while it runs.

        Muting must mean *stop*: not just "no new audio", but also no further
        thinking, no tool calls started and no reply spoken for something heard
        before the mute. Returns False if the turn was cancelled by a mute.
        """
        cancel = asyncio.Event()
        task = asyncio.create_task(self._handle(utterance, cancel), name="VoiceTurn")
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.1)
                if done:
                    task.result()  # re-raise a turn failure to the loop's error handler, as before
                    return True
                if self._gate.is_muted():
                    await self._abort_turn(task, cancel)
                    return False
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def _abort_turn(self, task: asyncio.Task, cancel: asyncio.Event) -> None:
        logger.info("[voice] muted mid-turn: cancelling the turn and stopping playback")
        cancel.set()  # lets a streaming model stop generating
        interrupt = getattr(self._speaker, "interrupt", None)
        if callable(interrupt):
            try:
                interrupt()
            except Exception as e:
                logger.warning(f"[voice] could not interrupt playback: {e}")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self._speaking = False
        self._collector.reset()
        self._reset_speaker_scoring()
        self._disarm(silent=True)
        self._emit("turn_cancelled", {"reason": "muted"})

    # -- Wake-word gating -------------------------------------------------

    def _is_armed(self) -> bool:
        return self._armed_until > time.monotonic()

    def _arm(self) -> None:
        was_armed = self._is_armed()
        self._armed_until = time.monotonic() + self._wake_window_sec
        if not was_armed:
            logger.info("[voice] wake trigger -> ARMED (listening window open)")
            self._emit("wake", {"armed": True})

    def _disarm(self, *, silent: bool = False) -> None:
        self._reset_speaker_scoring()
        if self._armed_until == 0.0:
            return
        self._armed_until = 0.0
        self._wake_buffer = b""
        if self._collector.is_speaking:
            self._collector.reset()
        if not silent:
            logger.info("[voice] wake window closed -> PASSIVE")
            self._emit("wake", {"armed": False})

    def _armed_expired(self) -> bool:
        return (
            self._armed_until > 0.0
            and time.monotonic() >= self._armed_until
            and not self._collector.is_speaking
        )

    def _wake_triggered(self, frame) -> bool:
        # Push-to-talk engines (hotkey) aren't frame-driven; they expose consume().
        consume = getattr(self._wake, "consume", None)
        if callable(consume) and self._wake_frame_bytes == 0:
            return bool(consume())
        if self._wake_frame_bytes <= 0:
            return False
        # Frame-driven engine (Porcupine): slice to its exact frame size.
        self._wake_buffer += frame.pcm
        while len(self._wake_buffer) >= self._wake_frame_bytes:
            chunk = self._wake_buffer[: self._wake_frame_bytes]
            self._wake_buffer = self._wake_buffer[self._wake_frame_bytes:]
            try:
                if self._wake.process(chunk):
                    self._wake_buffer = b""
                    return True
            except Exception as e:
                logger.warning(f"[voice] wake-word process failed: {e}")
                return False
        return False

    # -- Speaker recognition ------------------------------------------------

    def _feed_speaker_frame(self, frame) -> None:
        """Slice raw PCM into the recognizer's frame size and accumulate a
        running score per enrolled speaker. Mirrors _wake_triggered's
        buffering idiom - same reason: the mic callback's blocksize rarely
        matches a vendor SDK's required frame size exactly."""
        if self._speaker_frame_bytes <= 0:
            return
        self._speaker_buffer += frame.pcm
        while len(self._speaker_buffer) >= self._speaker_frame_bytes:
            chunk = self._speaker_buffer[: self._speaker_frame_bytes]
            self._speaker_buffer = self._speaker_buffer[self._speaker_frame_bytes:]
            try:
                scores = self._speaker_id.process(chunk)
            except Exception as e:
                logger.warning(f"[voice] speaker-id process failed: {e}")
                return
            if not scores:
                continue
            if not self._speaker_score_sums:
                self._speaker_score_sums = [0.0] * len(scores)
            for i, s in enumerate(scores):
                self._speaker_score_sums[i] += s
            self._speaker_score_count += 1

    def _resolve_speaker(self) -> tuple[str | None, bool]:
        """Mean score over the whole utterance, not a single frame - Eagle's
        scores stabilise with more audio, so deciding off one noisy frame
        would risk rejecting a real enrolled speaker, not just stray talk."""
        if self._speaker_id is None or not self._speaker_score_sums or self._speaker_score_count == 0:
            return None, False
        means = [s / self._speaker_score_count for s in self._speaker_score_sums]
        best_i = max(range(len(means)), key=lambda i: means[i])
        if means[best_i] < self._speaker_match_threshold:
            return None, False
        names = getattr(self._speaker_id, "speaker_names", [])
        name = names[best_i] if best_i < len(names) else None
        return name, name is not None

    def _reset_speaker_scoring(self) -> None:
        self._speaker_buffer = b""
        self._speaker_score_sums = []
        self._speaker_score_count = 0

    # -- One turn ---------------------------------------------------------

    async def _handle(self, utterance: Utterance, cancel: asyncio.Event | None = None) -> None:
        logger.info(f"[voice] utterance {utterance.duration_sec:.1f}s -> transcribing")
        self._emit("utterance", {"duration_sec": round(utterance.duration_sec, 2)})

        transcript = await self._stt.transcribe(utterance.audio)
        text = (transcript.text or "").strip()
        if len(text) < self._min_chars:
            logger.info("[voice] transcript too short; ignoring")
            return

        # Re-check: the user may have muted while we were transcribing.
        if self._gate.is_muted():
            logger.info("[voice] muted during transcription - dropping turn")
            return

        # Speech-to-text invents text on noise ("subscribe to our channel", "Bye. Bye. Bye..."):
        # that is not something the user said, so it must not become a turn.
        reason = artifact_reason(text, utterance.duration_sec)
        if reason is not None:
            logger.info(f"[voice] dropping probable STT artefact ({reason}): {text[:80]!r}")
            self._emit("artifact_ignored", {"reason": reason})
            return

        # Resolve who (if anyone enrolled) said this, then clear the running
        # scores so the next utterance starts clean. Reading before
        # resetting is safe - no other frames are fed while _handle runs,
        # this coroutine owns the only consumer of _tick's frame loop.
        speaker_name, speaker_known = self._resolve_speaker()
        self._reset_speaker_scoring()
        if self._require_known_speaker and self._speaker_id is not None and not speaker_known:
            logger.info("[voice] unrecognized speaker; dropping turn as stray talk")
            self._emit("speaker_rejected", {})
            return

        # Second line of defence against self-conversation. Even with correct
        # playback waiting, a loud room or an open speaker can bleed our own
        # words back in. If what we just "heard" is essentially what we just
        # said, it isn't a user turn.
        if self._echo_guard and self._is_echo(text):
            logger.warning(f"[voice] ignoring probable self-echo: {text[:60]!r}")
            self._emit("echo_ignored", {"text": text})
            return

        self._last_transcript = text
        logger.info(f"[voice] heard: {text!r}")
        self._emit("transcript", {"text": text})

        ctx = AgentContext(user_message=text, from_voice=True)
        if speaker_name:
            ctx.metadata["speaker_name"] = speaker_name
        if self._speak_replies and self._tts is not None:
            # Stream + speak per sentence so the first words play while the rest
            # of the reply is still generating (low time-to-first-audio).
            reply = await self._stream_and_speak(ctx, cancel)
        else:
            result = await self._supervisor.execute(ctx)
            reply = (result.response or "").strip()

        self._last_reply = reply
        self._turns += 1
        logger.info(f"[voice] reply: {reply[:120]!r}")
        self._emit("reply", {"text": reply})

    def _is_echo(self, heard: str) -> bool:
        """True if `heard` looks like our own last reply coming back."""
        if not self._last_reply:
            return False
        said = {w for w in _WORD_RE.findall(self._last_reply.lower()) if len(w) > 2}
        got = {w for w in _WORD_RE.findall(heard.lower()) if len(w) > 2}
        if not said or not got:
            return False
        overlap = len(said & got) / len(got)
        return overlap >= 0.6

    async def speak(self, text: str) -> None:
        """Speak arbitrary text through the same echo-guarded path as a reply."""
        await self._speak(text)

    async def _speak(self, text: str) -> None:
        """Synthesise and play with the mic held closed until playback truly ends."""
        self._speaking = True
        self._emit("speaking", {"text": text})
        try:
            if await self._say_pcm(text):
                await self._wait_playback()
        except Exception as e:
            logger.error(f"[voice] TTS failed: {e}")
        finally:
            await self._after_speaking()

    async def _stream_and_speak(self, ctx: AgentContext, cancel: asyncio.Event | None = None) -> str:
        """Stream the reply and speak each sentence as it completes.

        Speaking on sentence boundaries plays the first words while the rest of
        the reply is still generating — the dominant perceived-latency win for a
        CPU-bound voice assistant.
        """
        self._speaking = True
        self._emit("speaking", {"text": ""})
        parts: list[str] = []
        buffer = ""
        spoke_anything = False
        try:
            async for chunk in self._supervisor.execute_stream(ctx, cancel_event=cancel):
                if not chunk:
                    continue
                parts.append(chunk)
                buffer += chunk
                sentence, buffer = self._pop_sentence(buffer)
                while sentence:
                    spoke_anything = await self._say_pcm(sentence) or spoke_anything
                    sentence, buffer = self._pop_sentence(buffer)
            tail = buffer.strip()
            if tail:
                spoke_anything = await self._say_pcm(tail) or spoke_anything
            if spoke_anything:
                await self._wait_playback()
        except Exception as e:
            logger.error(f"[voice] streaming speak failed: {e}")
        finally:
            await self._after_speaking()
        return "".join(parts).strip()

    @staticmethod
    def _pop_sentence(buffer: str) -> tuple[str, str]:
        match = _SENTENCE_END_RE.search(buffer)
        if not match:
            return "", buffer
        cut = match.end()
        return buffer[:cut].strip(), buffer[cut:]

    async def _say_pcm(self, text: str) -> bool:
        spoke = False
        async for pcm in self._tts.synthesize(text):
            if pcm:
                self._speaker.play_pcm(pcm, self._tts.sample_rate)
                spoke = True
        return spoke

    async def _wait_playback(self) -> None:
        wait_done = getattr(self._speaker, "wait_done", None)
        if callable(wait_done):
            finished = await asyncio.to_thread(wait_done, self._speak_timeout_sec)
            if not finished:
                logger.warning("[voice] playback did not finish within timeout")
        else:
            # Adapter without completion signalling — conservative sleep rather
            # than reopening the mic blind.
            await asyncio.sleep(1.0)

    async def _after_speaking(self) -> None:
        # Settle THEN drain, so the tail of our own voice doesn't land in the
        # buffer after the drain. Then reopen the mic.
        await asyncio.sleep(self._post_speak_settle_sec)
        drain = getattr(self._audio, "drain", None)
        if callable(drain):
            drain()
        self._collector.reset()
        self._reset_speaker_scoring()
        self._speaking = False
        self._emit("idle", {})

    def _emit(self, kind: str, payload: dict) -> None:
        try:
            self._on_event({"kind": kind, "at": datetime.now().isoformat(), **payload})
        except Exception:
            pass