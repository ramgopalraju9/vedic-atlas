"""ToolManifestStorePort — where tool manifests and prompt files come from.

Two small ports in one file because they are always loaded together at boot:
the manifests describe the tools; the prompt store holds the versioned prompt
text the PromptComposer assembles. Adapters (tpa/filestore/) read them from
config/tools/*.yaml and config/prompts/*.md.
"""

from typing import Protocol, runtime_checkable

from domain.entities.tool_manifest import ToolManifest


@runtime_checkable
class ToolManifestStorePort(Protocol):
    def load_all(self) -> list[ToolManifest]:
        """Every tool manifest. Raises AppException(CONFIG_ERROR) on an invalid one."""
        ...


@runtime_checkable
class PromptStorePort(Protocol):
    def get(self, name: str) -> str:
        """Prompt text by name (e.g. "persona_lite"). Raises AppException(CONFIG_ERROR) if missing."""
        ...

    def names(self) -> list[str]:
        """All available prompt names."""
        ...
