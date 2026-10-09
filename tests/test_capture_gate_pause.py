from service.privacy.capture_gate import CaptureGate


class _MuteSwitch:
    source_name = "test"

    def __init__(self):
        self.muted = False
        self.callback = None

    def subscribe(self, callback):
        self.callback = callback

    def is_muted(self):
        return self.muted

    def sync_state(self, muted):
        self.muted = muted


class _Audio:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1


def test_capture_pause_preserves_privacy_mute_and_defers_reopening():
    switch = _MuteSwitch()
    audio = _Audio()
    gate = CaptureGate(switch, audio=audio)
    gate.start()

    assert audio.starts == 1
    gate.pause_capture()
    assert gate.is_muted() is False
    assert audio.stops == 1

    gate.set_muted(True, source="user")
    gate.set_muted(False, source="user")
    assert gate.is_muted() is False
    assert audio.starts == 1

    assert gate.resume_capture() is True
    assert audio.starts == 2
    assert gate.resume_capture() is True
    assert audio.starts == 2


def test_capture_pause_does_not_override_user_mute():
    switch = _MuteSwitch()
    audio = _Audio()
    gate = CaptureGate(switch, audio=audio)
    gate.start()
    gate.pause_capture()
    gate.set_muted(True, source="user")

    assert gate.resume_capture() is False
    assert gate.is_muted() is True
    assert audio.starts == 1
