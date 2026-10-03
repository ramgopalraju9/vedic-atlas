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
