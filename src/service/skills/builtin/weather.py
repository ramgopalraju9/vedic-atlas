"""GetWeatherSkill — current weather for a place (default: the configured home city)."""

from __future__ import annotations

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from service.lookup.weather_lookup import WeatherLookup
from service.skills.manifest_skill import ManifestSkill


class GetWeatherSkill(ManifestSkill):
    what = "get the weather"

    def __init__(self, lookup: WeatherLookup, manifest: ToolManifest, permission_level: str | None = None,
                 enabled: bool = True):
        super().__init__(manifest, permission_level=permission_level, enabled=enabled)
        self._lookup = lookup

    async def run(self, ctx: AgentContext, **params) -> SkillResult:
        obs = await self._lookup.get(params.get("place"))
        return self._from_observation(obs)
