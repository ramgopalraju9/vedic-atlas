"""Async helpers — timeout, retry, JSON extraction from model/tool output.

Donor: veda/utils/async_helpers.py. Copied near-verbatim: this file had
zero project-internal imports (only stdlib + veda.log), which is exactly
the bar for src/utilities/ per migration rule 9. Only the logger import
path changed. `extract_json` / `is_json_tool_call` remain useful for
parsing structured output from the local model (tool-call style JSON in
free text) — nothing here is cloud-CLI-specific.
"""

import asyncio
import json
import re
from typing import Any, Callable, TypeVar

from core.logging_config import logger

T = TypeVar("T")


async def async_timeout(coro, timeout_seconds: float, default: T = None) -> T:
    """Run a coroutine with a timeout, returning default on timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning(f"Operation timed out after {timeout_seconds}s")
        return default


async def async_retry(
    coro_factory: Callable[[], Any],
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
) -> Any:
    """Retry a coroutine factory with exponential backoff.

    Args:
        coro_factory: Callable that returns a new coroutine each call.
        max_attempts: Maximum number of attempts.
        delay: Initial delay between retries in seconds.
        backoff: Multiplier applied to delay after each retry.
    """
    last_error: Exception | None = None
    current_delay = delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                logger.warning(
                    f"Attempt {attempt}/{max_attempts} failed: {e}. Retrying in {current_delay}s"
                )
                await asyncio.sleep(current_delay)
                current_delay *= backoff
            else:
                logger.error(f"All {max_attempts} attempts failed. Last error: {e}")

    raise last_error


def extract_json(text: str) -> dict | list | None:
    """Extract the first JSON object or array from a text string.

    Useful for parsing local-model output that may contain JSON mixed with
    prose. Tries a direct parse first, then falls back to pattern search.
    """
    text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    match = re.search(r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def is_json_tool_call(text: str) -> bool:
    """Check if text looks like a JSON tool-call payload."""
    parsed = extract_json(text)
    return isinstance(parsed, dict) and "tool" in parsed and "input" in parsed