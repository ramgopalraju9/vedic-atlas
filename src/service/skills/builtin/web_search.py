"""WebSearchSkill — real web search for current facts and news (Tavily)."""

from __future__ import annotations

import re

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from service.lookup.search_lookup import SearchLookup
from service.skills.manifest_skill import ManifestSkill


# Tavily's "news" topic only returns recent articles, which is wrong for questions like "upcoming
# movies in October" (it returned streaming-platform articles, not the films). Use it only when the
# user's own words ask for news; everything else is a general search.
_NEWS_WORDS = re.compile(r"\b(news|headlines?|breaking|latest|today|yesterday|last night|this week)\b", re.IGNORECASE)


class WebSearchSkill(ManifestSkill):
    what = "search the web"

    def __init__(self, lookup: SearchLookup, manifest: ToolManifest, permission_level: str | None = None,
                 enabled: bool = True):
        super().__init__(manifest, permission_level=permission_level, enabled=enabled)
        self._lookup = lookup

    async def run(self, ctx: AgentContext, **params) -> SkillResult:
        topic = params.get("topic") or "general"
        if topic == "news" and not _NEWS_WORDS.search(ctx.user_message or ""):
            logger.info("[web_search] model chose topic=news but the question isn't news-like; using general")
            topic = "general"
        obs = await self._lookup.search(params.get("query"), topic)
        return self._from_observation(obs)
