import asyncio
from datetime import datetime

from domain.entities.agent_result import AgentResult
from domain.entities.utterance import Utterance
from domain.value_objects.transcript import Transcript
from service.voice.voice_session import VoiceSession


class _Collector:
    def __init__(self):
        self.is_speaking = False

    def reset(self):
        pass


class _Frame:
    def __init__(self, pcm: bytes):
        self.pcm = pcm


class _FakeSpeakerRecognizer:
    """Scripted multi-speaker recognizer: pops one score-list per process() call."""

    frame_length = 4  # samples -> 8 bytes at int16
    sample_rate = 16000
    speaker_names = ["alice", "bob"]

    def __init__(self, scripted_scores):
        self._scripted = list(scripted_scores)

    def process(self, chunk: bytes):
        if self._scripted:
            return self._scripted.pop(0)
        return [0.0, 0.0]

    def reload_profiles(self):
        pass


class _FakeSTT:
    async def transcribe(self, audio):
        return Transcript(text="hello there", confidence=1.0)


class _FakeSupervisor:
    def __init__(self):
        self.last_ctx = None

    async def execute(self, ctx):
        self.last_ctx = ctx
        return AgentResult(agent_name="responder", response="hi")


class _FakeGate:
    def is_muted(self):
        return False


def _session_with_speaker(rec, *, threshold: float = 0.6) -> VoiceSession:
    return VoiceSession(
        audio=object(), collector=_Collector(), stt=object(), tts=object(),
        speaker=object(), supervisor=object(), capture_gate=object(),
        speaker_id=rec, speaker_match_threshold=threshold,
    )


def _full_session(rec, *, require_known: bool) -> VoiceSession:
    return VoiceSession(
        audio=object(), collector=_Collector(), stt=_FakeSTT(), tts=object(),
        speaker=object(), supervisor=_FakeSupervisor(), capture_gate=_FakeGate(),
        speak_replies=False,
        speaker_id=rec, require_known_speaker=require_known, speaker_match_threshold=0.6,
    )


def _utterance() -> Utterance:
    return Utterance(audio=object(), started_at=datetime.now(), duration_sec=0.5, frame_count=1)


def test_feed_speaker_frame_accumulates_and_resolves():
    rec = _FakeSpeakerRecognizer([[0.9, 0.1], [0.7, 0.2], [0.8, 0.0]])
    s = _session_with_speaker(rec)
    frame_bytes = b"\x00" * (rec.frame_length * 2)
    s._feed_speaker_frame(_Frame(frame_bytes))
    s._feed_speaker_frame(_Frame(frame_bytes))
    s._feed_speaker_frame(_Frame(frame_bytes))
    name, known = s._resolve_speaker()
    assert known is True
    assert name == "alice"  # mean alice = 0.8, bob = 0.1


def test_resolve_speaker_below_threshold_is_unknown():
    rec = _FakeSpeakerRecognizer([[0.2, 0.1]])
    s = _session_with_speaker(rec, threshold=0.6)
    s._feed_speaker_frame(_Frame(b"\x00" * (rec.frame_length * 2)))
    name, known = s._resolve_speaker()
    assert known is False
    assert name is None


def test_resolve_speaker_without_engine_returns_unknown():
    s = _session_with_speaker(None)
    assert s._resolve_speaker() == (None, False)


def test_reset_speaker_scoring_clears_state():
    rec = _FakeSpeakerRecognizer([[0.9, 0.1]])
    s = _session_with_speaker(rec)
    s._feed_speaker_frame(_Frame(b"\x00" * (rec.frame_length * 2)))
    assert s._speaker_score_count == 1
    s._reset_speaker_scoring()
    assert s._speaker_score_count == 0
    assert s._speaker_score_sums == []


def test_unrecognized_speaker_dropped_when_required():
    rec = _FakeSpeakerRecognizer([[0.1, 0.1]])
    s = _full_session(rec, require_known=True)
    s._feed_speaker_frame(_Frame(b"\x00" * (rec.frame_length * 2)))
    asyncio.run(s._handle(_utterance()))
    assert s._supervisor.last_ctx is None  # dropped before reaching the agent


def test_unrecognized_speaker_allowed_when_not_required():
    rec = _FakeSpeakerRecognizer([[0.1, 0.1]])
    s = _full_session(rec, require_known=False)
    s._feed_speaker_frame(_Frame(b"\x00" * (rec.frame_length * 2)))
    asyncio.run(s._handle(_utterance()))
    assert s._supervisor.last_ctx is not None
    assert "speaker_name" not in s._supervisor.last_ctx.metadata


def test_recognized_speaker_tags_context():
    rec = _FakeSpeakerRecognizer([[0.9, 0.1]])
    s = _full_session(rec, require_known=True)
    s._feed_speaker_frame(_Frame(b"\x00" * (rec.frame_length * 2)))
    asyncio.run(s._handle(_utterance()))
    assert s._supervisor.last_ctx is not None
    assert s._supervisor.last_ctx.metadata["speaker_name"] == "alice"
