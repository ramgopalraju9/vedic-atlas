"""Voice loop: mute interrupts the turn in progress, STT artefacts are dropped, wake word fails closed.

No audio hardware or model: fakes only. Run: pytest tests/test_voice_mute_and_artifacts.py
"""

import asyncio
import time
from datetime import datetime

import pytest

from domain.entities.agent_result import AgentResult
from domain.entities.utterance import Utterance
from domain.policies.transcript_policy import artifact_reason
from domain.value_objects.audio_window import AudioWindow
from domain.value_objects.transcript import Transcript
from service.voice.voice_session import VoiceSession


# ---- transcript policy -------------------------------------------------------

HALLUCINATIONS = [
    ("Yes. If you want to see more of this video, please subscribe to our channel. Thank you. "
     "We'll see you next week. " + "Bye. " * 25, 1.2),
    ("So I am going to help you and help you with a good time. I am going to help you with a good time "
     "with a good time. I'm going to help you with a good time.", 15.0),
    ("Bye. Bye. Bye. Bye. Bye. Bye.", 3.0),
    ("Thanks for watching!", 2.0),
    ("[music]", 2.0),
    ("(applause)", 1.0),
    ("♪ ♪", 1.0),
    ("Thank you.", 0.8),
    ("you", 0.5),
    ("", 1.0),
]
GENUINE = [
    ("what are my tasks", 2.0), ("Can you tell me the upcoming movies this month from tollywood?", 4.0),
    ("yes", 0.9), ("no no no", 2.0), ("remove the milk task", 2.0), ("Thank you.", 3.0),
    ("Okay, so I have several tasks in my pocket. What are the tasks I have?", 6.0),
    ("I need to buy milk, milk, and more milk", 3.0), ("one two three four five six", 3.0),
]


@pytest.mark.parametrize("text,dur", HALLUCINATIONS, ids=[f"artefact{i}" for i in range(len(HALLUCINATIONS))])
def test_stt_artefacts_are_recognised(text, dur):
    assert artifact_reason(text, dur) is not None


@pytest.mark.parametrize("text,dur", GENUINE)
def test_genuine_speech_is_never_dropped(text, dur):
    assert artifact_reason(text, dur) is None


# ---- fakes -----------------------------------------------------------------

class _Gate:
    def __init__(self):
        self.muted = False

    def is_muted(self):
        return self.muted


class _Collector:
    is_speaking = False

    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


class _Speaker:
    def __init__(self):
        self.interrupted = 0

    def interrupt(self):
        self.interrupted += 1


class _STT:
    def __init__(self, text):
        self._text = text

    async def transcribe(self, audio):
        return Transcript(text=self._text, confidence=1.0)


class _Supervisor:
    def __init__(self, work_sec=0.0):
        self.calls = 0
        self.cancel_events = []
        self._work = work_sec

    async def execute(self, ctx):
        self.calls += 1
        await asyncio.sleep(self._work)
        return AgentResult(agent_name="responder", response="ok")

    async def execute_stream(self, ctx, cancel_event=None):
        self.calls += 1
        self.cancel_events.append(cancel_event)
        await asyncio.sleep(self._work)
        yield "hello."


def _utterance(sec=2.0):
    now = datetime.now()
    window = AudioWindow(pcm=b"\x00\x00" * 160, sample_rate=16000, channels=1, started_at=now, duration_sec=sec)
    return Utterance(audio=window, started_at=now, duration_sec=sec, frame_count=1)


def _session(gate, supervisor, stt_text="what are my tasks", speaker=None, collector=None):
    return VoiceSession(
        audio=object(), collector=collector or _Collector(), stt=_STT(stt_text), tts=None,
        speaker=speaker or _Speaker(), supervisor=supervisor, capture_gate=gate, speak_replies=False,
    )


# ---- artefacts never become turns ---------------------------------------------

def test_a_hallucinated_transcript_does_not_reach_the_assistant():
    sup = _Supervisor()
    session = _session(_Gate(), sup, stt_text="Please subscribe to our channel. Bye. Bye. Bye. Bye. Bye. Bye.")
    asyncio.run(session._handle(_utterance(1.2)))
    assert sup.calls == 0 and session.status()["turns"] == 0


def test_a_real_transcript_still_gets_an_answer():
    sup = _Supervisor()
    session = _session(_Gate(), sup)
    asyncio.run(session._handle(_utterance()))
    assert sup.calls == 1 and session.status()["turns"] == 1


# ---- mute interrupts the turn ----------------------------------------------------

def test_mute_mid_turn_cancels_it_and_stops_playback():
    async def scenario():
        gate, speaker, collector = _Gate(), _Speaker(), _Collector()
        sup = _Supervisor(work_sec=5.0)  # a long-running turn (slow model / tool)
        session = _session(gate, sup, speaker=speaker, collector=collector)
        turn = asyncio.create_task(session._run_turn(_utterance()))
        await asyncio.sleep(0.25)       # the turn is now in flight
        gate.muted = True               # the user mutes
        started = time.monotonic()
        completed = await asyncio.wait_for(turn, timeout=3)
        return completed, time.monotonic() - started, speaker, collector, session

    completed, elapsed, speaker, collector, session = asyncio.run(scenario())
    assert completed is False                 # reported as cancelled
    assert elapsed < 1.0                      # stopped promptly, not after the 5 s of work
    assert speaker.interrupted == 1           # playback cut off
    assert collector.resets >= 1
    assert session.status()["turns"] == 0 and session.status()["speaking"] is False


def test_the_streaming_model_is_told_to_stop_on_mute():
    async def scenario():
        gate = _Gate()
        sup = _Supervisor(work_sec=5.0)
        session = VoiceSession(
            audio=object(), collector=_Collector(), stt=_STT("what are my tasks"), tts=_FakeTTS(),
            speaker=_PlayingSpeaker(), supervisor=sup, capture_gate=gate, speak_replies=True,
        )
        turn = asyncio.create_task(session._run_turn(_utterance()))
        await asyncio.sleep(0.25)
        gate.muted = True
        await asyncio.wait_for(turn, timeout=3)
        return sup

    sup = asyncio.run(scenario())
    assert sup.cancel_events and sup.cancel_events[0].is_set()  # generation is cancelled, not left running


def test_an_unmuted_turn_runs_to_completion():
    async def scenario():
        sup = _Supervisor(work_sec=0.2)
        session = _session(_Gate(), sup)
        return await session._run_turn(_utterance()), sup

    completed, sup = asyncio.run(scenario())
    assert completed is True and sup.calls == 1


class _FakeTTS:
    sample_rate = 22050

    async def synthesize(self, text):
        yield b"\x00\x00" * 10


class _PlayingSpeaker(_Speaker):
    def play_pcm(self, pcm, rate):
        pass

    def wait_done(self, timeout):
        return True


# ---- SpeakerPlayback.interrupt ----------------------------------------------------

def test_playback_interrupt_drops_queued_audio_and_goes_idle(monkeypatch):
    import sys
    import types

    from tpa.audio.playback import SpeakerPlayback

    stopped = []
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(stop=lambda: stopped.append(1)))
    sp = SpeakerPlayback()  # worker deliberately not started: chunks just queue up
    for _ in range(3):
        sp.play_pcm(b"\x00\x00", 16000)
    assert sp.is_playing
    sp.interrupt()
    assert not sp.is_playing and stopped == [1]
    assert sp._queue.empty()


# ---- wake word fails closed ---------------------------------------------------------

def test_a_configured_wake_word_that_cannot_start_disables_voice_instead_of_listening_always(monkeypatch):
    import server
    from core.config import load_full_config

    cfg = load_full_config().model_copy(deep=True)
    cfg.audio.voice_enabled, cfg.audio.wake_word_enabled, cfg.audio.wake_engine = True, True, "openwakeword"
    for name in ("_build_vad", "_build_stt", "_build_tts", "_build_speaker"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: object())
    monkeypatch.setattr(server, "_build_wake_word", lambda _cfg: None)  # the model failed to load
    session = server._build_voice_session(cfg, audio=object(), capture_gate=_Gate(), supervisor=object(), event_bus=object())
    assert session is None
