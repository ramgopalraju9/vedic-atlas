"""TTS engine selection: auto-picks per platform, and every problem is a clear boot-time error.

No audio is produced. Run: pytest tests/test_tts_selection.py
"""

import json
import sys
from pathlib import Path

import pytest

import server
from core.config import load_full_config
from tpa.tts.piper import PiperProvider
from tpa.tts.pyttsx3_provider import Pyttsx3Provider


@pytest.fixture
def cfg():
    c = load_full_config().model_copy(deep=True)
    c.audio.tts_engine, c.audio.tts_model_path = "auto", None
    return c


@pytest.fixture
def errors(monkeypatch):
    """Capture server.logger.error messages."""
    seen: list[str] = []
    monkeypatch.setattr(server.logger, "error", lambda msg, *a, **k: seen.append(str(msg)))
    return seen


def _voice(tmp_path: Path, rate: int | None = 22050) -> Path:
    model = tmp_path / "en_US-test-medium.onnx"
    model.write_bytes(b"fake-onnx")
    if rate is not None:
        (tmp_path / "en_US-test-medium.onnx.json").write_text(json.dumps({"audio": {"sample_rate": rate}}), encoding="utf-8")
    return model


# ---- auto ---------------------------------------------------------------------

def test_auto_on_windows_uses_windows_speech(cfg, monkeypatch):
    monkeypatch.setattr(server, "_IS_WINDOWS", True)
    assert isinstance(server._build_tts(cfg), Pyttsx3Provider)


def test_auto_off_windows_wants_piper_and_a_voice(cfg, monkeypatch, errors):
    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    assert server._resolve_tts_engine(cfg) == "piper"
    assert server._build_tts(cfg) is None
    assert any("Piper needs a voice" in e and "tts_model_path" in e for e in errors)


def test_auto_off_windows_with_a_voice_builds_piper(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    cfg.audio.tts_model_path = str(_voice(tmp_path))
    assert isinstance(server._build_tts(cfg), PiperProvider)


def test_a_relative_voice_path_is_resolved_from_the_project_root_not_the_cwd(cfg, monkeypatch):
    from core.constants import PROJECT_ROOT

    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    rel = "data/models/piper/does-not-exist.onnx"
    cfg.audio.tts_model_path = rel
    captured = {}
    monkeypatch.setattr("tpa.tts.piper.PiperProvider.__init__", lambda self, model_path, **k: captured.setdefault("p", Path(model_path)))
    server._build_tts(cfg)
    assert captured["p"] == PROJECT_ROOT / rel


# ---- explicit engines ----------------------------------------------------------------

def test_explicit_piper_without_a_voice_falls_back_on_windows_only(cfg, monkeypatch, errors):
    cfg.audio.tts_engine = "piper"
    monkeypatch.setattr(server, "_IS_WINDOWS", True)
    assert isinstance(server._build_tts(cfg), Pyttsx3Provider)
    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    assert server._build_tts(cfg) is None and errors


def test_piper_with_a_missing_voice_file_is_a_clear_error(cfg, monkeypatch, errors, tmp_path):
    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    cfg.audio.tts_model_path = str(tmp_path / "nope.onnx")
    assert server._build_tts(cfg) is None
    assert any("Piper voice model not found" in e for e in errors)


def test_pyttsx3_missing_on_linux_says_it_is_windows_only(cfg, monkeypatch, errors):
    cfg.audio.tts_engine = "pyttsx3"
    monkeypatch.setitem(sys.modules, "pyttsx3", None)  # makes `import pyttsx3` raise ImportError
    assert server._build_tts(cfg) is None
    assert any("Windows-only" in e and "piper" in e for e in errors)


def test_piper_package_missing_says_how_to_install_it(cfg, monkeypatch, errors, tmp_path):
    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    cfg.audio.tts_model_path = str(_voice(tmp_path))
    monkeypatch.setitem(sys.modules, "piper", None)
    assert server._build_tts(cfg) is None
    assert any("pip install piper-tts" in e for e in errors)


def test_unknown_engine_is_rejected(cfg, errors):
    cfg.audio.tts_engine = "espeak"
    assert server._build_tts(cfg) is None
    assert any("unknown audio.tts_engine" in e for e in errors)


def test_a_tts_failure_stops_voice_at_startup_not_per_reply(cfg, monkeypatch):
    monkeypatch.setattr(server, "_IS_WINDOWS", False)
    cfg.audio.voice_enabled = True
    for name in ("_build_vad", "_build_stt", "_build_speaker"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: object())
    session = server._build_voice_session(cfg, audio=object(), capture_gate=object(), supervisor=object(), event_bus=object())
    assert session is None  # "voice not started - missing: tts"


# ---- Piper sample rate -----------------------------------------------------------------

def test_piper_reads_the_voices_own_sample_rate(tmp_path):
    low, medium = tmp_path / "low", tmp_path / "medium"
    low.mkdir(), medium.mkdir()
    assert PiperProvider(_voice(low, rate=16000)).sample_rate == 16000
    assert PiperProvider(_voice(medium, rate=22050)).sample_rate == 22050


def test_piper_falls_back_to_the_default_rate_without_a_voice_config(tmp_path):
    assert PiperProvider(_voice(tmp_path, rate=None)).sample_rate == 22050


def test_piper_still_rejects_a_missing_voice_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        PiperProvider(tmp_path / "nope.onnx")
