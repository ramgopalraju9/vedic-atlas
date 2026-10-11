"""Config loader — reads config/*.yaml into a cached AppConfig.

Donor: veda/config.py's `load_full_config`/`reload_full_config`/
`ensure_dirs`, read in full and adapted for the 9-file split (see
schemas/config_schemas.py's docstring for the full section-by-section
mapping). Where the donor read one `config.yaml`, this reads one YAML
file per section from `config/` and merges them into a single AppConfig;
a section file that doesn't exist on disk just uses that section's
Pydantic defaults.
"""

from __future__ import annotations

import os

import yaml

from core.constants import AUDIT_DIR, CONFIG_DIR, DATA_DIR, TEMP_DIR
from core.env import load_env
from schemas.config_schemas import AppConfig

_SECTION_FILES = (
    "app", "inference", "embedding", "audio", "sensing", "reminders", "privacy",
    "governance", "guardrails", "agents", "prompting", "skills",
)

_full_config_cache: AppConfig | None = None


def _read_yaml(path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _read_section(name: str) -> dict:
    return _read_yaml(CONFIG_DIR / f"{name}.yaml")


def active_profile() -> str:
    """The profile name from VEDA_PROFILE, or "" for the base config.

    Read from the process environment or from PROJECT_ROOT/.env (a real environment variable wins over .env).
    The .env file is loaded here, before the first config read, so every entry point (veda, uvicorn,
    `veda server`, scripts) sees the same profile.
    """
    load_env()
    return os.environ.get("VEDA_PROFILE", "").strip()


def _profile_overlay() -> dict:
    """Per-model settings kept apart from the base config, so switching model never edits the base values.

    `VEDA_PROFILE=qwen3-0.6b` merges config/profiles/qwen3-0.6b.yaml (top-level keys are section names,
    e.g. `inference:` / `app:`) over the base files; unset means the base config unchanged.
    """
    profile = active_profile()
    if not profile:
        return {}
    path = CONFIG_DIR / "profiles" / f"{profile}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"VEDA_PROFILE={profile!r} but {path} does not exist")
    return _read_yaml(path)


def load_full_config() -> AppConfig:
    """Load the complete multi-section config from config/*.yaml.

    Cached after first load — each file is read once per process. Call
    `reload_full_config()` to force a re-read (e.g. after an admin edit).
    """
    global _full_config_cache
    if _full_config_cache is not None:
        return _full_config_cache

    raw = {name: _read_section(name) for name in _SECTION_FILES}
    for name, overrides in _profile_overlay().items():
        raw[name] = {**raw.get(name, {}), **(overrides or {})}
    _full_config_cache = AppConfig(**{k: v for k, v in raw.items() if v})
    return _full_config_cache


def reload_full_config() -> AppConfig:
    """Force re-read of config/*.yaml."""
    global _full_config_cache
    _full_config_cache = None
    return load_full_config()


def ensure_dirs() -> None:
    """Create runtime directories if they don't exist. Safe to call every startup."""
    DATA_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)
    AUDIT_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "speaker_profiles").mkdir(exist_ok=True)
    (DATA_DIR / "models" / "openwakeword").mkdir(parents=True, exist_ok=True)