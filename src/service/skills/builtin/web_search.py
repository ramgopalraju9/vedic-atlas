"""WebSearchSkill — real web search for current facts and news (Tavily)."""

from __future__ import annotations

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from service.lookup.search_lookup import SearchLookup
from service.skills.manifest_skill import ManifestSkill


class WebSearchSkill(ManifestSkill):
    what = "search the web"

    def __init__(self, lookup: SearchLookup, manifest: ToolManifest, permission_level: str | None = None,
                 enabled: bool = True):
        super().__init__(manifest, permission_level=permission_level, enabled=enabled)
        self._lookup = lookup

    async def run(self, ctx: AgentContext, **params) -> SkillResult:
        obs = await self._lookup.search(params.get("query"), params.get("topic") or "general")
        return self._from_observation(obs)
