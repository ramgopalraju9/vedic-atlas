"""FileOpsSkill — file system operations with path allow/block list enforcement.

Donor: veda/skills/builtin/file_ops.py, read in full and ported verbatim
except exception/context import paths. `PATH_BLOCKED` restored to
core/enums.py specifically because this file needs it (see the ledger's
Batch 6 correction note).
"""

import asyncio
from pathlib import Path

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from core.enums import ErrorMessage, ExceptionCode
from exceptions.exception import AppException
from core.logging_config import logger
from service.skills.base_skill import BaseSkill

VALID_ACTIONS = ("read", "write", "list", "exists", "delete")
MAX_READ_LENGTH = 10000


class FileOpsSkill(BaseSkill):
    """Perform file system operations: read, write, list, exists, delete."""

    def __init__(
        self,
        permission_level: str = "notify",
        enabled: bool = True,
        allowed_paths: list[str] | None = None,
        blocked_paths: list[str] | None = None,
    ):
        super().__init__(
            name="file_ops",
            description="Perform file system operations: read, write, list, check existence, or delete files",
            permission_level=permission_level,
            enabled=enabled,
        )
        self.allowed_paths = [Path(p).expanduser().resolve() for p in (allowed_paths or ["~"])]
        self.blocked_paths = [Path(p).expanduser().resolve() for p in (blocked_paths or [])]

    def get_parameters_description(self) -> str:
        return (
            "Parameters: action (str, required: read|write|list|exists|delete), "
            "path (str, required), content (str, optional — for write action)"
        )

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(VALID_ACTIONS)},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["action", "path"],
        }

    def is_path_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        for blocked in self.blocked_paths:
            try:
                resolved.relative_to(blocked)
                return False
            except ValueError:
                continue
        for allowed in self.allowed_paths:
            try:
                resolved.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        action = params.get("action", "")
        path_str = params.get("path", "")
        content = params.get("content", "")

        if action not in VALID_ACTIONS:
            return SkillResult(skill_name=self.name, success=False, error=f"Invalid action '{action}'")
        if not path_str:
            return SkillResult(skill_name=self.name, success=False, error="No path provided")

        target = Path(path_str).expanduser()

        if not self.is_path_allowed(target):
            raise AppException(
                class_name="FileOpsSkill",
                code=ExceptionCode.PATH_BLOCKED,
                error_message=ErrorMessage.PATH_IS_BLOCKED,
                path=str(target),
            )

        logger.info(f"File operation: {action} on {target}")
        try:
            if action == "read":
                return await self._read(target)
            elif action == "write":
                return await self._write(target, content)
            elif action == "list":
                return await self._list(target)
            elif action == "exists":
                return await self._exists(target)
            elif action == "delete":
                return await self._delete(target)
        except AppException:
            raise
        except Exception as e:
            logger.error(f"File operation '{action}' failed on {target}: {e}")
            return SkillResult(skill_name=self.name, success=False, error=str(e))

    async def _read(self, path: Path) -> SkillResult:
        def _do():
            if not path.exists():
                return SkillResult(skill_name=self.name, success=False, error=f"File not found: {path}")
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_READ_LENGTH:
                text = text[:MAX_READ_LENGTH] + "\n... (truncated)"
            return SkillResult(skill_name=self.name, success=True, output=text)
        return await asyncio.to_thread(_do)

    async def _write(self, path: Path, content: str) -> SkillResult:
        def _do():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return SkillResult(skill_name=self.name, success=True, output=f"Written {len(content)} chars to {path}")
        return await asyncio.to_thread(_do)

    async def _list(self, path: Path) -> SkillResult:
        def _do():
            if not path.exists():
                return SkillResult(skill_name=self.name, success=False, error=f"Directory not found: {path}")
            if not path.is_dir():
                return SkillResult(skill_name=self.name, success=False, error=f"Not a directory: {path}")
            entries = [f"{'[DIR] ' if i.is_dir() else '      '}{i.name}" for i in sorted(path.iterdir())]
            return SkillResult(skill_name=self.name, success=True, output="\n".join(entries) or "(empty directory)")
        return await asyncio.to_thread(_do)

    async def _exists(self, path: Path) -> SkillResult:
        exists = path.exists()
        kind = "file" if (exists and path.is_file()) else ("directory" if (exists and path.is_dir()) else "unknown")
        return SkillResult(
            skill_name=self.name, success=True,
            output=f"{'Exists' if exists else 'Does not exist'}: {path}" + (f" ({kind})" if exists else ""),
        )

    async def _delete(self, path: Path) -> SkillResult:
        def _do():
            if not path.exists():
                return SkillResult(skill_name=self.name, success=False, error=f"File not found: {path}")
            if path.is_dir():
                return SkillResult(skill_name=self.name, success=False, error=f"Cannot delete directory: {path}")
            path.unlink()
            return SkillResult(skill_name=self.name, success=True, output=f"Deleted: {path}")
        return await asyncio.to_thread(_do)