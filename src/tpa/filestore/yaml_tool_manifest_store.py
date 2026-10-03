"""YamlToolManifestStore — loads tool manifests from config/tools/*.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from core.constants import CONFIG_DIR
from core.enums import ErrorMessage, ExceptionCode
from domain.entities.tool_manifest import ToolManifest
from exceptions.exception import AppException
from schemas.tool_manifest_schema import ToolManifestSchema

_DEFAULT_DIR = CONFIG_DIR / "tools"


class YamlToolManifestStore:
    """Implements ToolManifestStorePort."""

    def __init__(self, directory: str | Path | None = None):
        self._dir = Path(directory) if directory else _DEFAULT_DIR

    def load_all(self) -> list[ToolManifest]:
        manifests: list[ToolManifest] = []
        seen: set[str] = set()
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                manifest = ToolManifestSchema(**raw).to_domain()
            except (ValidationError, TypeError, yaml.YAMLError) as e:
                raise AppException(
                    class_name="YamlToolManifestStore",
                    code=ExceptionCode.CONFIG_ERROR,
                    error_message=ErrorMessage.TOOL_MANIFEST_INVALID,
                    name=path.name,
                    detail=str(e).replace("\n", " ")[:300],
                ) from e
            if manifest.name in seen:
                raise AppException(
                    class_name="YamlToolManifestStore",
                    code=ExceptionCode.CONFIG_ERROR,
                    error_message=ErrorMessage.TOOL_MANIFEST_INVALID,
                    name=path.name,
                    detail=f"duplicate tool name '{manifest.name}'",
                )
            seen.add(manifest.name)
            manifests.append(manifest)
        return manifests
