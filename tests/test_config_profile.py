"""VEDA_PROFILE picks a config/profiles/<name>.yaml overlay; it can come from the shell or from .env."""

import pytest

import core.config as config
import core.env as env_module


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No real .env, no real VEDA_PROFILE, and no leaked config cache in or out of the test."""
    monkeypatch.delenv("VEDA_PROFILE", raising=False)
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)  # load_env() now reads tmp_path/.env (absent)
    config._full_config_cache = None
    yield
    config._full_config_cache = None


def test_no_profile_means_the_base_config():
    assert config.active_profile() == ""
    cfg = config.load_full_config()
    assert cfg.app.chat_summaries == 3 and cfg.app.chat_persona == "full"


def test_profile_from_the_environment_overrides_the_base(monkeypatch):
    monkeypatch.setenv("VEDA_PROFILE", "qwen3-0.6b")
    cfg = config.load_full_config()
    assert config.active_profile() == "qwen3-0.6b"
    assert "0.6B" in cfg.inference.model_path
    assert cfg.app.chat_summaries == 0 and cfg.app.chat_persona == "compact"
    assert cfg.inference.n_ctx == 2048
    assert cfg.embedding.top_k == 1


def test_profile_from_the_dotenv_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text('TAVILY_API_KEY=""\r\nVEDA_PROFILE="qwen3-1.7b"\r\n', encoding="utf-8")
    assert config.active_profile() == "qwen3-1.7b"
    assert "1.7B" in config.load_full_config().inference.model_path


def test_a_real_environment_variable_beats_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("VEDA_PROFILE=qwen3-1.7b\n", encoding="utf-8")
    monkeypatch.setenv("VEDA_PROFILE", "qwen3-0.6b")
    assert config.active_profile() == "qwen3-0.6b"


def test_an_empty_profile_in_dotenv_is_the_base_config(tmp_path):
    (tmp_path / ".env").write_text('VEDA_PROFILE=""\n', encoding="utf-8")
    assert config.active_profile() == ""
    assert config.load_full_config().app.chat_persona == "full"


def test_an_unknown_profile_fails_loudly(monkeypatch):
    monkeypatch.setenv("VEDA_PROFILE", "qwen3-nope")
    with pytest.raises(FileNotFoundError, match="qwen3-nope"):
        config.load_full_config()


@pytest.mark.parametrize("name", ["qwen3-0.6b", "qwen3-1.7b", "qwen3-4b"])
def test_every_shipped_profile_loads(monkeypatch, name):
    monkeypatch.setenv("VEDA_PROFILE", name)
    cfg = config.load_full_config()
    assert cfg.inference.n_ctx == 2048 and cfg.app.max_history == 4 and cfg.app.chat_persona == "compact"
