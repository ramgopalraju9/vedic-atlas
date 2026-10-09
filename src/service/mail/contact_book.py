"""Load the optional name-to-address map used by Gmail drafting."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.constants import CONFIG_DIR
from domain.policies.mail_policy import is_address

_DEFAULT_PATH = CONFIG_DIR / "mail_contacts.yaml"


def load_mail_contacts(path: str | Path = _DEFAULT_PATH) -> dict[str, str]:
    """Load configured contact names and validate each email address.

    A missing file is equivalent to an empty contact map; malformed configured
    data raises an error so drafts cannot silently use an unintended address.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        contacts = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid Gmail contact map in {path}: {exc}") from exc

    if not isinstance(contacts, dict):
        raise ValueError(f"Invalid Gmail contact map in {path}: expected a name-to-email map")

    validated: dict[str, str] = {}
    for name, address in contacts.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid Gmail contact map in {path}: contact names must be non-empty strings")
        if not isinstance(address, str) or not is_address(address.strip()):
            raise ValueError(f"Invalid Gmail contact map in {path}: invalid email address for {name!r}")
        validated[name.strip()] = address.strip()
    return validated
