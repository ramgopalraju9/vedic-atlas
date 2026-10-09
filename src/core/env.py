"""Environment loading — reads PROJECT_ROOT/.env into os.environ (without overriding real env vars).

API keys (e.g. TAVILY_API_KEY) live in .env, which is git-ignored; config files
only ever hold the *name* of the variable.
"""

from core.constants import PROJECT_ROOT


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is a declared dependency, but never block boot on it
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def reload_env(prefix: str) -> list[str]:
    """Re-read PROJECT_ROOT/.env and let its `prefix*` entries replace what the running process has. Used after the Google
    sign-in writes a new refresh token, so the server picks it up without a restart. Returns the names that were set."""
    import os

    try:
        from dotenv import dotenv_values
    except ImportError:
        return []
    updated = []
    for key, value in dotenv_values(PROJECT_ROOT / ".env").items():
        if key.startswith(prefix) and value is not None:
            os.environ[key] = value
            updated.append(key)
    return updated
