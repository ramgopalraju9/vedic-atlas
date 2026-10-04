"""Microphones that refuse 16 kHz: Veda must open them at their own rate and convert.

Regression: on a Raspberry Pi with a "USB PnP Sound Device" (accepts only 44100 / 48000 Hz) the unmute worked
but the wake word never fired, because Veda opened the mic at 16 kHz and the open was refused.

No hardware needed: `sounddevice` is replaced by a fake that accepts only the rates it is given.
Run: pytest tests/test_mic_fallback.py
"""

import sys
import types

import numpy as np
import pytest

from tpa.audio.resampler import Int16BlockResampler
from tpa.audio.sounddevice_capture import SoundDeviceCapture

FRAME = 480   # 30 ms at 16 kHz


def _sine(rate: int, seconds: float, freq: float = 440.0, amp: int = 8000) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _dominant_hz(samples: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
    return float(np.argmax(spectrum) * rate / samples.size)


# ---- the resampler -----------------------------------------------------------------------------

@pytest.mark.parametrize("rate", [48_000, 44_100, 32_000, 22_050])
def test_every_block_comes_out_as_exactly_one_16k_frame(rate):
    rs = Int16BlockResampler(rate, 16_000, FRAME)
    block = _sine(rate, rs.in_frames / rate)
    assert len(block) == rs.in_frames
    assert all(len(rs.process(block.tobytes())) == FRAME * 2 for _ in range(5))


@pytest.mark.parametrize("rate", [48_000, 44_100])
def test_pitch_and_loudness_survive_the_conversion(rate):
    rs = Int16BlockResampler(rate, 16_000, FRAME)
    source = _sine(rate, 1.0)
    out = np.concatenate([
        np.frombuffer(rs.process(source[i:i + rs.in_frames].tobytes()), dtype=np.int16)
        for i in range(0, len(source) - rs.in_frames + 1, rs.in_frames)
    ])
    assert abs(_dominant_hz(out, 16_000) - 440.0) < 5.0
    assert 6500 < np.abs(out[200:-200]).max() < 9500          # amplitude about 8000, no clipping or collapse


def test_tones_above_the_16k_nyquist_are_filtered_not_folded_back():
    rs = Int16BlockResampler(48_000, 16_000, FRAME)
    source = _sine(48_000, 0.5, freq=12_000)                   # inaudible to a 16 kHz recogniser; would alias to 4 kHz
    out = np.concatenate([
        np.frombuffer(rs.process(source[i:i + rs.in_frames].tobytes()), dtype=np.int16)
        for i in range(0, len(source) - rs.in_frames + 1, rs.in_frames)
    ])
    assert np.abs(out[200:-200]).max() < 1500


@pytest.mark.parametrize("rate", [48_000, 44_100])
def test_block_edges_do_not_glitch(rate):
    # A continuous tone must stay continuous across block boundaries: no sample-to-sample jump bigger than the
    # tone itself allows (a zero-padded filter edge would show up as a click every 30 ms).
    rs = Int16BlockResampler(rate, 16_000, FRAME)
    source = _sine(rate, 0.6, freq=300, amp=8000)
    out = np.concatenate([
        np.frombuffer(rs.process(source[i:i + rs.in_frames].tobytes()), dtype=np.int16)
        for i in range(0, len(source) - rs.in_frames + 1, rs.in_frames)
    ]).astype(np.int32)
    steady = out[FRAME * 2:]                                       # skip the first blocks (filter warm-up)
    biggest_step = np.abs(np.diff(steady)).max()
    assert biggest_step < 2 * np.pi * 300 / 16_000 * 8000 * 1.3    # the tone's own maximum slope, plus 30%


# ---- the capture adapter -----------------------------------------------------------------------

class _FakeStream:
    def __init__(self, rate, blocksize, callback, latency=None):
        self.rate, self.blocksize, self.callback, self.latency = rate, blocksize, callback, latency
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        pass

    def feed(self, seconds: float = 0.12):
        block = _sine(self.rate, self.blocksize / self.rate)
        for _ in range(max(1, round(seconds / (self.blocksize / self.rate)))):
            self.callback(block.tobytes(), self.blocksize, None, None)


def _fake_sounddevice(monkeypatch, accepted_rates, default_rate=48_000):
    streams = []
    module = types.ModuleType("sounddevice")

    def raw_input_stream(samplerate, channels, dtype, blocksize, device, callback, latency=None):
        if samplerate not in accepted_rates:
            raise RuntimeError(f"Error opening RawInputStream: Invalid sample rate [PaErrorCode -9997] ({samplerate})")
        stream = _FakeStream(samplerate, blocksize, callback, latency)
        streams.append(stream)
        return stream

    module.RawInputStream = raw_input_stream
    module.query_devices = lambda device=None, kind=None: {"default_samplerate": default_rate}
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return streams


def test_a_mic_that_accepts_16k_is_opened_directly_with_no_conversion(monkeypatch):
    streams = _fake_sounddevice(monkeypatch, accepted_rates={16_000, 48_000})
    cap = SoundDeviceCapture(frame_length=FRAME)
    cap.start()
    assert streams[0].rate == 16_000 and cap._resampler is None


def test_a_48k_only_mic_is_opened_at_48k_and_delivers_16k_frames(monkeypatch):
    streams = _fake_sounddevice(monkeypatch, accepted_rates={44_100, 48_000})
    cap = SoundDeviceCapture(frame_length=FRAME)
    cap.start()
    stream = streams[-1]
    assert stream.rate == 48_000 and stream.started
    assert stream.latency == "high"
    stream.feed()
    window = cap.read(timeout=0.1)
    assert window is not None and window.sample_rate == 16_000 and window.channels == 1
    assert len(window.pcm) == FRAME * 2
    assert abs(_dominant_hz(np.frombuffer(window.pcm, dtype=np.int16), 16_000) - 440.0) < 40.0


def test_a_44k_only_mic_works_too(monkeypatch):
    streams = _fake_sounddevice(monkeypatch, accepted_rates={44_100}, default_rate=44_100)
    cap = SoundDeviceCapture(frame_length=FRAME)
    cap.start()
    streams[-1].feed()
    assert streams[-1].rate == 44_100 and len(cap.read(timeout=0.1).pcm) == FRAME * 2


def test_the_device_default_rate_is_tried_before_the_common_ones(monkeypatch):
    streams = _fake_sounddevice(monkeypatch, accepted_rates={22_050, 48_000}, default_rate=22_050)
    SoundDeviceCapture(frame_length=FRAME).start()
    assert streams[-1].rate == 22_050


def test_a_mic_that_accepts_nothing_still_fails_loudly(monkeypatch):
    _fake_sounddevice(monkeypatch, accepted_rates=set())
    with pytest.raises(RuntimeError, match="Invalid sample rate"):
        SoundDeviceCapture(frame_length=FRAME).start()


def test_restarting_after_a_mute_reopens_cleanly(monkeypatch):
    streams = _fake_sounddevice(monkeypatch, accepted_rates={48_000})
    cap = SoundDeviceCapture(frame_length=FRAME)
    cap.start(); cap.stop(); cap.start()
    streams[-1].feed()
    assert len(streams) == 2 and len(cap.read(timeout=0.1).pcm) == FRAME * 2
