from tpa.audio.sounddevice_capture import SoundDeviceCapture


def test_drain_discards_queued_frames_and_reports_queue_overflow_drops():
    capture = SoundDeviceCapture(frame_length=320)
    capture._queue.put_nowait(b"frame-1")
    capture._queue.put_nowait(b"frame-2")
    capture._queue_dropped_frames = 3

    assert capture.drain() == 5
    assert capture.drain() == 0
    assert capture._queue.empty()
