"""RouterPolicy — thin service-layer adapter over the domain routing policy.

★ NEW. Wraps domain.policies.routing_policy so the supervisor doesn't call
a bare function directly — if the routing strategy ever needs state
(caching, per-agent hit-rate stats) this is where it goes, without
touching the pure function itself.
"""

from domain.entities.agent_profile import AgentProfile
from domain.policies.routing_policy import match_agent, pick_agent


class RouterPolicy:
    def match(self, message: str, candidates: tuple[AgentProfile, ...]) -> str | None:
        """The deterministic pick, or None when no rule matched (so an LLM router may decide)."""
        return match_agent(message, candidates)

    def pick(self, message: str, candidates: tuple[AgentProfile, ...], default: str) -> str | None:
        return pick_agent(message, candidates, default)