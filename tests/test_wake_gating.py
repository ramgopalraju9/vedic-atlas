import time

from service.voice.voice_session import VoiceSession


class _Collector:
    def __init__(self):
        self.is_speaking = False
        self.reset_called = 0

    def reset(self):
        self.reset_called += 1


class _HotkeyWake:
    frame_length = 0
    sample_rate = 0

    def __init__(self):
        self._pressed = False

    def consume(self) -> bool:
        if self._pressed:
            self._pressed = False
            return True
        return False


class _FrameWake:
    frame_length = 4  # 4 samples -> 8 bytes at int16
    sample_rate = 16000

    def __init__(self):
        self.calls = 0

    def process(self, chunk: bytes) -> bool:
        self.calls += 1
        return self.calls == 2  # fire on the second full frame


class _Frame:
    def __init__(self, pcm: bytes):
        self.pcm = pcm


def _session(wake) -> VoiceSession:
    return VoiceSession(
        audio=object(),
        collector=_Collector(),
        stt=object(),
        tts=object(),
        speaker=object(),
        supervisor=object(),
        capture_gate=object(),
        wake_word=wake,
        wake_engine="test",
        wake_window_sec=1.0,
    )


def test_hotkey_consume_arms_once():
    w = _HotkeyWake()
    s = _session(w)
    assert s._wake_triggered(_Frame(b"")) is False
    w._pressed = True
    assert s._wake_triggered(_Frame(b"")) is True
    assert s._wake_triggered(_Frame(b"")) is False  # press consumed


def test_frame_accumulator_slices_and_fires():
    w = _FrameWake()
    s = _session(w)
    # frame_bytes = 8; 6 bytes is not a full frame yet.
    assert s._wake_triggered(_Frame(b"\x00" * 6)) is False
    assert w.calls == 0
    # 10 more -> 16 buffered -> two 8-byte frames; fires on the second.
    assert s._wake_triggered(_Frame(b"\x00" * 10)) is True
    assert w.calls == 2
    assert s._wake_buffer == b""  # buffer cleared on fire


def test_arm_disarm_and_expiry():
    s = _session(_HotkeyWake())
    assert s._is_armed() is False
    s._arm()
    assert s._is_armed() is True
    s._armed_until = time.monotonic() - 1  # force the window into the past
    assert s._armed_expired() is True
    s._disarm()
    assert s._is_armed() is False