"""ConvertCurrencySkill — convert an amount between currencies at the latest reference rate."""

from __future__ import annotations

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from service.lookup.currency_lookup import CurrencyLookup
from service.skills.manifest_skill import ManifestSkill


class ConvertCurrencySkill(ManifestSkill):
    what = "convert that"

    def __init__(self, lookup: CurrencyLookup, manifest: ToolManifest, permission_level: str | None = None,
                 enabled: bool = True):
        super().__init__(manifest, permission_level=permission_level, enabled=enabled)
        self._lookup = lookup

    async def run(self, ctx: AgentContext, **params) -> SkillResult:
        obs = await self._lookup.convert(params.get("amount"), params.get("from"), params.get("to"))
        return self._from_observation(obs)
