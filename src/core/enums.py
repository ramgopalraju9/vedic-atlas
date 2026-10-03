"""All enumerations for the offline personal companion.

Donor: veda/core/enums.py — split cleanup:
  - Dropped SKILL_* / APPROVAL_* / COMMAND_BLOCKED / PATH_BLOCKED codes tied
    to the code-generation agent (out of scope) and moved permission/approval
    codes into the domain layer's own ExceptionCode.
  - Dropped AgentType.CODER / COMMUNICATOR (code agent + Teams are out of scope).
  - PermissionLevel and AgentType are VALUE OBJECTS, not core concerns — they
    are re-declared in src/domain/value_objects/ (see that package) and kept
    here only as re-exports for code that hasn't migrated yet. If nothing
    imports them from core after the port, delete the re-export block below.

Correction (2026-09-22): the first read of this donor file during Batch 1
stopped at line 99 and missed the rest — the real file is 134 lines and
also declares SkillCategory and HookEvent. Added both here once discovered
while grounding Batch 6's skills/hooks work. HookEvent has a confirmed
consumer (veda/service/skill_service.py fires PRE_SKILL/POST_SKILL hooks) —
this was not optional, it would have broken service/skills/skill_runner.py.
Correction (2026-09-22, second pass): the initial Batch 1 pruning also
dropped PATH_BLOCKED and COMMAND_BLOCKED on the assumption they were
code-agent-only. Wrong — service/skills/builtin/file_ops.py and
terminal.py (both in-scope, general-purpose skills, not the code agent)
raise these directly. Added back.
"""

from enum import Enum


class ExceptionCode(str, Enum):
    """Error codes for AppException. One-to-one with an HTTP status —
    see exceptions/handlers.py for the mapping table."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INFERENCE_ERROR = "INFERENCE_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    AGENT_ROUTING_ERROR = "AGENT_ROUTING_ERROR"
    SKILL_EXECUTION_ERROR = "SKILL_EXECUTION_ERROR"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    COMMAND_BLOCKED = "COMMAND_BLOCKED"
    PATH_BLOCKED = "PATH_BLOCKED"
    EGRESS_DENIED = "EGRESS_DENIED"  # new — NetworkPolicy / egress_guard denial
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"  # provider down / API key not configured

    def __repr__(self):
        return self.value

    def __str__(self):
        return self.value


class ErrorMessage(Enum):
    """Templated error messages — format with **kwargs."""

    INFERENCE_TIMEOUT = "Local model timed out after {timeout}s"
    INFERENCE_FAILURE = "Local model inference failed: {detail}"
    KNOWLEDGE_NOT_FOUND = "Fact at index {index} not found"
    CONFIG_LOAD_FAILED = "Failed to load config from {path}: {detail}"
    GENERIC = "{detail}"
    AGENT_ROUTING_FAILED = "Failed to route request to agent: {detail}"
    SKILL_EXEC_FAILED = "Skill '{skill_name}' execution failed: {detail}"
    SKILL_NOT_REGISTERED = "Skill '{skill_name}' is not registered"
    SKILL_DISABLED = "Skill '{skill_name}' is disabled"
    PERMISSION_DENIED = "Permission denied for skill '{skill_name}': requires '{level}' approval"
    RATE_LIMITED = "Rate limit exceeded for skill '{skill_name}'"
    APPROVAL_TIMED_OUT = "Approval request timed out after {timeout}s"
    APPROVAL_WAS_REJECTED = "User rejected skill '{skill_name}' execution"
    COMMAND_IS_BLOCKED = "Command '{command}' is blocked by guardrails"
    PATH_IS_BLOCKED = "Path '{path}' is not in allowed paths"
    EGRESS_DENIED = "Outbound call to '{host}' denied — not on the allow-list"
    TOOL_MANIFEST_INVALID = "Invalid tool manifest '{name}': {detail}"
    PROMPT_NOT_FOUND = "Prompt '{name}' not found in {path}"
    TOOL_UNAVAILABLE = "Tool '{tool}' is unavailable: {detail}"

    def __repr__(self):
        return self.value

    def __str__(self):
        return self.value


class SkillCategory(str, Enum):
    """Skill categories for grouping in prompts/UI.

    Donor also had VISION — dropped, vision is out of scope.
    """

    TERMINAL = "terminal"
    FILE = "file"
    IDE = "ide"
    SEARCH = "search"
    COMMUNICATION = "communication"
    CUSTOM = "custom"

    def __repr__(self):
        return self.value

    def __str__(self):
        return self.value


class HookEvent(str, Enum):
    """Events that hooks can listen to. Consumed by service/skills/skill_runner.py."""

    PRE_SKILL = "pre_skill"
    POST_SKILL = "post_skill"
    ON_ERROR = "on_error"
    ON_AGENT_START = "on_agent_start"
    ON_AGENT_COMPLETE = "on_agent_complete"
    ON_APPROVAL_NEEDED = "on_approval_needed"

    def __repr__(self):
        return self.value

    def __str__(self):
        return self.value