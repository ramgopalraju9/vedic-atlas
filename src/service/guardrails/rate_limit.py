"""RateLimiter — token-bucket rate limiter for skills.

Donor: veda/guardrails/rate_limiter.py, read in full and ported verbatim
except the exception import path.
"""

import time

from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException
from core.logging_config import logger


class _TokenBucket:
    def __init__(self, max_tokens: int, refill_period: float = 60.0):
        self.max_tokens = max_tokens
        self.refill_period = refill_period
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + (elapsed / self.refill_period) * self.max_tokens)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Rate limiter with global and per-skill limits (token-bucket, per-minute refill)."""

    def __init__(self, enabled: bool = True, global_rpm: int = 30, per_skill_rpm: int = 10):
        self.enabled = enabled
        self.global_rpm = global_rpm
        self.per_skill_rpm = per_skill_rpm
        self._global_bucket = _TokenBucket(global_rpm)
        self._skill_buckets: dict[str, _TokenBucket] = {}

    def _get_skill_bucket(self, skill_name: str) -> _TokenBucket:
        if skill_name not in self._skill_buckets:
            self._skill_buckets[skill_name] = _TokenBucket(self.per_skill_rpm)
        return self._skill_buckets[skill_name]

    def check(self, skill_name: str) -> bool:
        """Returns True if allowed. Raises AppException if rate limited."""
        if not self.enabled:
            return True
        if not self._global_bucket.consume():
            logger.warning(f"Global rate limit exceeded ({self.global_rpm} rpm)")
            raise AppException(
                class_name="RateLimiter", code=ExceptionCode.RATE_LIMITED,
                error_message=ErrorMessage.RATE_LIMITED, skill_name=f"global ({skill_name})",
            )
        bucket = self._get_skill_bucket(skill_name)
        if not bucket.consume():
            logger.warning(f"Per-skill rate limit exceeded for '{skill_name}' ({self.per_skill_rpm} rpm)")
            raise AppException(
                class_name="RateLimiter", code=ExceptionCode.RATE_LIMITED,
                error_message=ErrorMessage.RATE_LIMITED, skill_name=skill_name,
            )
        return True