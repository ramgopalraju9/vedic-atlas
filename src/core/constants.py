"""Project-level constants for the offline personal companion.

No cloud paths, no code-agent workspace root, no vision file paths —
only what the target architecture actually uses.
"""

from pathlib import Path

# Project metadata
PROJECT_NAME = "Veda"
VERSION = "0.1.0"
API_PREFIX = "/api"

# Project paths (this file lives at src/core/constants.py — one level up to root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
TEMP_DIR = DATA_DIR / "temp"
MODEL_DIR = DATA_DIR / "models"
AUDIT_DIR = DATA_DIR / "audit"
CONFIG_DIR = PROJECT_ROOT / "config"