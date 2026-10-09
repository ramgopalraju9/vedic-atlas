import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from domain.value_objects.audio_window import AudioWindow
from tpa.stt.faster_whisper import FasterWhisperProvider
from tpa.stt.noise_suppression import SpectralNoiseSuppressor


def test_spectral_suppression_reduces_steady_noise_and_preserves_speech():
    sample_rate = 16_000
    time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
    clean = np.zeros_like(time)
    active = (time >= 0.5) & (time < 1.5)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * time[active]) ** 2
    clean[active] = 0.3 * envelope * np.sin(2 * np.pi * 220 * time[active])

    rng = np.random.default_rng(17)
    noise = rng.normal(0.0, 0.06, clean.size).astype(np.float32)
    noisy = clean + noise
    output = SpectralNoiseSuppressor(strength=0.75).process(noisy)

    before_error = noisy[active] - clean[active]
    after_error = output[active] - clean[active]
    before_snr = 10 * np.log10(np.mean(clean[active] ** 2) / np.mean(before_error ** 2))
    after_snr = 10 * np.log10(np.mean(clean[active] ** 2) / np.mean(after_error ** 2))
    assert after_snr >= before_snr + 1.5
    assert np.sqrt(np.mean(output[active] ** 2)) > 0.1
    assert np.isfinite(output).all()
    assert output.shape == noisy.shape
    assert np.array_equal(noisy, clean + noise)


def test_spectral_suppressor_rejects_invalid_strength():
    with pytest.raises(ValueError, match="between 0 and 1"):
        SpectralNoiseSuppressor(strength=1.1)


def test_zero_strength_returns_an_unchanged_copy():
    signal = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    output = SpectralNoiseSuppressor(strength=0.0).process(signal)
    assert np.array_equal(output, signal)
    assert output is not signal


def test_provider_suppresses_the_audio_before_sending_it_to_whisper(caplog):
    sample_rate = 16_000
    samples = np.random.default_rng(23).normal(0, 0.08, sample_rate).astype(np.float32)
    pcm = (samples * 32767).astype(np.int16).tobytes()
    received = []

    class FakeModel:
        def transcribe(self, audio, **_kwargs):
            received.append(audio.copy())
            segment = SimpleNamespace(
                text="hello",
                start=0.0,
                end=0.5,
                avg_logprob=-0.2,
                no_speech_prob=0.01,
            )
            return iter([segment]), SimpleNamespace(language_probability=0.99)

    provider = FasterWhisperProvider(noise_suppression_enabled=True)
    provider._model = FakeModel()
    audio = AudioWindow(
        pcm=pcm,
        sample_rate=sample_rate,
        channels=1,
        started_at=datetime.now(),
        duration_sec=1.0,
    )

    with caplog.at_level(logging.INFO, logger="veda"):
        transcript = asyncio.run(provider.transcribe(audio))

    assert transcript.text == "hello"
    assert len(received) == 1
    original = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    assert not np.array_equal(received[0], original)
    assert "input_rms=" in caplog.text and "input_peak=" in caplog.text
    assert "output_rms=" in caplog.text and "output_peak=" in caplog.text
