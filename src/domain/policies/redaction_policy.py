"""RedactionPolicy — detects PII and credentials that must not be persisted or logged.

Donor: veda/guardrails/validators.py's OutputValidator, read in full. The
regex patterns below are copied verbatim from the donor's `_PII_PATTERNS`
and `_CREDENTIAL_PATTERNS` tables. The donor wrapped these in an async
BaseGuardrail with length-limit checking mixed in; only the pattern-match
decision is domain logic. Length-limit validation and the guardrail
orchestration are staged at service/guardrails/validators.py.
"""

import re

_PII_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
    (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "credit card"),
    (r"\b[A-Z]{5}\d{4}[A-Z]\b", "PAN card"),
    (r"\b\d{12}\b", "Aadhaar"),
)

_CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)(api[_-]?key|apikey)\s*[:=]\s*\S+", "API key"),
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+", "password"),
    (r"(?i)(secret|token)\s*[:=]\s*\S+", "secret/token"),
    (r"(?i)(aws_access_key_id|aws_secret_access_key)\s*[:=]\s*\S+", "AWS credential"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub PAT"),
)


def find_pii(content: str) -> str | None:
    """Return the matched PII type's label, or None if content looks clean."""
    for pattern, label in _PII_PATTERNS:
        if re.search(pattern, content):
            return label
    return None


def find_credential(content: str) -> str | None:
    """Return the matched credential type's label, or None if content looks clean."""
    for pattern, label in _CREDENTIAL_PATTERNS:
        if re.search(pattern, content):
            return label
    return None