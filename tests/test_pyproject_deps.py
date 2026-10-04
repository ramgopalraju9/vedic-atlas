"""pyproject.toml dependency rules that keep a Raspberry Pi install working.

Regression: openwakeword declares `tflite-runtime` on Linux, which has no wheels for Python 3.12+, so
`pip install -e .` failed on the Pi ("No matching distribution found for tflite-runtime"). Veda only runs the
ONNX wake-word model, so on Linux openwakeword is installed with --no-deps by scripts/setup_pi.sh instead, and
the packages it really needs are declared directly.
"""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DEPS = [Requirement(d) for d in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]]

PI = {"sys_platform": "linux", "platform_system": "Linux", "platform_machine": "aarch64", "os_name": "posix", "python_version": "3.13"}
WINDOWS = {"sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64", "os_name": "nt", "python_version": "3.12"}


def installed_on(env: dict) -> set[str]:
    return {r.name.lower() for r in DEPS if r.marker is None or r.marker.evaluate(env)}


def test_openwakeword_is_not_pulled_in_by_pip_on_linux():
    assert "openwakeword" not in installed_on(PI)       # it would drag in tflite-runtime
    assert "openwakeword" in installed_on(WINDOWS)      # Windows has no such dependency


def test_the_packages_openwakeword_really_needs_are_declared_on_every_platform():
    needed = {"onnxruntime", "scipy", "scikit-learn", "tqdm", "requests", "numpy"}
    assert needed <= installed_on(PI)
    assert needed <= installed_on(WINDOWS)


def test_windows_only_packages_are_skipped_on_the_pi():
    windows_only = {"pyttsx3", "pywin32", "pycaw"}
    assert windows_only <= installed_on(WINDOWS)
    assert not (windows_only & installed_on(PI))


def test_semantic_memory_needs_only_onnxruntime_and_tokenizers_not_fastembed():
    # fastembed's extra native packages (mmh3, py-rust-stemmers) are blocked on some machines and are not needed
    assert {"tokenizers", "onnxruntime"} <= installed_on(PI)
    assert {"tokenizers", "onnxruntime"} <= installed_on(WINDOWS)
    assert "fastembed" not in installed_on(PI) | installed_on(WINDOWS)


def test_the_pi_gets_the_default_stack():
    assert {"llama-cpp-python", "faster-whisper", "sounddevice", "piper-tts", "onnxruntime", "fastapi"} <= installed_on(PI)


def test_the_setup_script_installs_openwakeword_without_dependencies_on_linux():
    script = (ROOT / "scripts" / "setup_pi.sh").read_text(encoding="utf-8")
    assert 'pip" install --no-deps --prefer-binary "openwakeword' in script
    assert b"\r" not in (ROOT / "scripts" / "setup_pi.sh").read_bytes()  # CRLF would break the script on Linux
