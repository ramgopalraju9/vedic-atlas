"""faster-whisper must still load when PyAV's native files are blocked (Windows Smart App Control)."""

import sys
import types

import pytest

from tpa.stt.faster_whisper import allow_unavailable_av


@pytest.fixture(autouse=True)
def _restore_av():
    saved = {k: v for k, v in sys.modules.items() if k == "av" or k.startswith("av.")}
    yield
    for k in [k for k in sys.modules if k == "av" or k.startswith("av.")]:
        del sys.modules[k]
    sys.modules.update(saved)


def test_a_working_pyav_is_left_alone():
    real = types.ModuleType("av")
    sys.modules["av"] = real
    assert allow_unavailable_av() is False
    assert sys.modules["av"] is real


def test_a_blocked_pyav_is_replaced_by_a_stand_in():
    sys.modules["av"] = None  # makes `import av` raise ImportError, like a blocked DLL
    sys.modules["av.video"] = types.ModuleType("av.video")  # half-imported leftovers must go too
    assert allow_unavailable_av() is True
    import av

    assert isinstance(av, types.ModuleType)
    assert "av.video" not in sys.modules


def test_the_stand_in_explains_itself_when_something_tries_to_decode_a_file():
    sys.modules["av"] = None
    allow_unavailable_av()
    import av

    with pytest.raises(AttributeError, match="PyAV is unavailable"):
        av.open("clip.wav")


def test_the_stand_in_does_not_break_a_second_call():
    sys.modules["av"] = None
    assert allow_unavailable_av() is True
    assert allow_unavailable_av() is False  # now `import av` succeeds with the stand-in
