"""openwakeword needs two helper models that its pip package does not ship.

Regression: on the Raspberry Pi `python scripts/wake_test.py` (and the server's wake engine) failed with
"onnxruntime NO_SUCHFILE ... openwakeword/resources/models/melspectrogram.onnx", so the wake word never loaded and
voice stayed off - which looked like a microphone problem. scripts/setup_pi.sh now downloads them, and the engine
reports the missing files with the fix instead of an onnxruntime trace.

Run: pytest tests/test_wake_feature_models.py
"""

from pathlib import Path

import pytest

from tpa.wake_word.openwakeword_engine import FEATURE_MODEL_FILES, require_feature_models

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_pi.sh"


def _package(tmp_path: Path, present=()) -> Path:
    models = tmp_path / "openwakeword" / "resources" / "models"
    models.mkdir(parents=True)
    for name in present:
        (models / name).write_bytes(b"x")
    return tmp_path / "openwakeword"


def test_both_helper_models_present_is_fine(tmp_path):
    require_feature_models(_package(tmp_path, FEATURE_MODEL_FILES))


@pytest.mark.parametrize("present", [(), ("melspectrogram.onnx",), ("embedding_model.onnx",)])
def test_a_missing_helper_model_is_named_with_the_fix(tmp_path, present):
    with pytest.raises(FileNotFoundError) as err:
        require_feature_models(_package(tmp_path, present))
    message = str(err.value)
    for name in set(FEATURE_MODEL_FILES) - set(present):
        assert name in message
    assert "setup_pi.sh" in message and "download_models" in message


def test_the_engine_stops_at_the_clear_error_before_loading_anything(tmp_path, monkeypatch):
    import tpa.wake_word.openwakeword_engine as engine

    model = tmp_path / "hey_veda.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(engine, "require_feature_models", lambda package_dir=None: (_ for _ in ()).throw(FileNotFoundError("helper models missing")))
    with pytest.raises(FileNotFoundError, match="helper models missing"):
        engine.OpenWakeWordEngine(model_path=str(model))


def test_the_setup_script_downloads_both_helper_models_into_the_package():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "OWW_FEATURE_FILES=(melspectrogram.onnx embedding_model.onnx)" in script
    assert "github.com/dscripka/openWakeWord/releases/download/v0.5.1" in script
    assert "download_wake_feature_models" in script.split("download_models() {")[1].split("\n}\n")[0]   # part of the models step
    assert b"\r" not in SCRIPT.read_bytes()
