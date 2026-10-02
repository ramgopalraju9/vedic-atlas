import asyncio
import re
from pathlib import Path

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException
from core.logging_config import logger
from service.skills.base_skill import BaseSkill


class TerminalSkill(BaseSkill):
    """Execute shell commands via subprocess, with a block list and timeout."""

    def __init__(
        self,
        permission_level: str = "approve",
        enabled: bool = True,
        working_directory: str = "~",
        timeout: int = 30,
        blocked_commands: list[str] | None = None,
        blocked_patterns: list[str] | None = None,
    ):
        super().__init__(
            name="terminal",
            description="Execute a shell command in the system terminal",
            permission_level=permission_level,
            enabled=enabled,
        )
        self.working_directory = working_directory
        self.timeout = timeout
        self.blocked_commands = blocked_commands or []
        self.blocked_patterns = [re.compile(p) for p in (blocked_patterns or [])]

    def get_parameters_description(self) -> str:
        return "Parameters: command (str, required), working_directory (str, optional)"

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "working_directory": {"type": "string"},
            },
            "required": ["command"],
        }

    def is_command_blocked(self, command: str) -> str | None:
        cmd_stripped = command.strip()
        for blocked in self.blocked_commands:
            if blocked in cmd_stripped:
                return f"matches blocked command '{blocked}'"
        for pattern in self.blocked_patterns:
            if pattern.search(cmd_stripped):
                return f"matches blocked pattern '{pattern.pattern}'"
        return None

    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        command = params.get("command", "")
        cwd = params.get("working_directory", self.working_directory)

        if not command:
            return SkillResult(skill_name=self.name, success=False, error="No command provided")

        block_reason = self.is_command_blocked(command)
        if block_reason:
            raise AppException(
                class_name="TerminalSkill",
                code=ExceptionCode.COMMAND_BLOCKED,
                error_message=ErrorMessage.COMMAND_IS_BLOCKED,
                command=command,
            )

        resolved_cwd = str(Path(cwd).expanduser())
        logger.info(f"Executing command: {command} (cwd: {resolved_cwd})")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=resolved_cwd,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            output = stdout_text
            if stderr_text:
                output += f"\n[stderr]: {stderr_text}" if output else f"[stderr]: {stderr_text}"
            if len(output) > 10000:
                output = output[:10000] + "\n... (output truncated)"

            success = process.returncode == 0
            return SkillResult(
                skill_name=self.name,
                success=success,
                output=output,
                error=f"Exit code {process.returncode}" if not success else None,
            )
        except asyncio.TimeoutError:
            return SkillResult(skill_name=self.name, success=False, error=f"Command timed out after {self.timeout}s")
        except Exception as e:
            return SkillResult(skill_name=self.name, success=False, error=str(e))