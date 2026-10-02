"""InputValidator / OutputValidator — length and content checks on guardrail boundaries.

Donor: veda/guardrails/validators.py, read in full and ported verbatim.
The PII/credential PATTERN MATCHING itself is a pure function and lives
at domain/policies/redaction_policy.py (Batch 4) — this class is the
async orchestration wrapper (length limits + calling that pure function).
"""

from domain.entities.agent_context import AgentContext
from domain.policies.redaction_policy import find_credential, find_pii
from core.logging_config import logger
from service.guardrails.base import BaseGuardrail, GuardrailResult


class InputValidator(BaseGuardrail):
    """Validates input content — checks length limits."""

    def __init__(self, max_input_length: int = 10000):
        super().__init__(name="input_validator")
        self.max_input_length = max_input_length

    async def validate(self, ctx: AgentContext, **kwargs) -> GuardrailResult:
        content = kwargs.get("content", ctx.user_message)
        if len(content) > self.max_input_length:
            logger.warning(f"Input exceeds max length: {len(content)} > {self.max_input_length}")
            return GuardrailResult(passed=False, reason=f"Input exceeds maximum length ({len(content)} > {self.max_input_length})")
        return GuardrailResult(passed=True)


class OutputValidator(BaseGuardrail):
    """Validates output content — detects PII, credentials, and checks length."""

    def __init__(self, block_pii: bool = True, block_credentials: bool = True, max_output_length: int = 50000):
        super().__init__(name="output_validator")
        self.block_pii = block_pii
        self.block_credentials = block_credentials
        self.max_output_length = max_output_length

    async def validate(self, ctx: AgentContext, **kwargs) -> GuardrailResult:
        content = kwargs.get("content", "")
        if not content:
            return GuardrailResult(passed=True)

        if len(content) > self.max_output_length:
            logger.warning(f"Output exceeds max length: {len(content)} > {self.max_output_length}")
            return GuardrailResult(passed=False, reason=f"Output exceeds maximum length ({len(content)} > {self.max_output_length})")

        if self.block_pii:
            pii_type = find_pii(content)
            if pii_type:
                logger.warning(f"Potential {pii_type} detected in output")
                return GuardrailResult(passed=False, reason=f"Potential {pii_type} detected in output")

        if self.block_credentials:
            cred_type = find_credential(content)
            if cred_type:
                logger.warning(f"Potential {cred_type} detected in output")
                return GuardrailResult(passed=False, reason=f"Potential {cred_type} detected in output")

        return GuardrailResult(passed=True)