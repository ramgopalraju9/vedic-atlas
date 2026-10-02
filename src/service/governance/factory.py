"""Governance factory — picks the configured GovernanceProvider.

Donor: veda/governance/factory.py, read in full. Changes:
  - The `agt` branch is a hard reject: `agent-governance-toolkit` is an
    external dependency (FILE_MAP.md marks veda/governance/adapters/agt.py
    ⛔). Requesting it raises instead of silently falling back, matching
    the precedent set by tpa/inference/factory.py's UnsupportedBackendError
    for cloud inference backends.
  - Takes an already-constructed `audit_sink: AuditSinkPort` rather than
    building a concrete SQLite/Null backend itself — keeps this factory
    (service/) from needing to import anything from tpa/. The caller
    (server.py, Batch 11) decides sqlite vs null and constructs it.
  - Returns `None` for "disabled", not a NullProvider object — matching
    the `GovernanceProvider | None` shape already used by
    service/agent/supervisor.py and controller/routes/governance.py. A
    concrete always-allow `NullProvider` still exists, but only as a test
    double (tests/fakes/null_governance.py) — see FILE_MAP.md's own note
    that its donor location was "a better home" there.
"""

from __future__ import annotations

from domain.ports.audit_sink_port import AuditSinkPort
from domain.ports.governance_port import GovernanceProvider
from service.governance.policy_evaluator import PolicyEvaluator


class UnsupportedGovernanceProviderError(ValueError):
    pass


def build_governance(config, audit_sink: AuditSinkPort) -> GovernanceProvider | None:
    """Build the governance provider from config. `None` means disabled.

    Args:
        config: GovernanceConfig-shaped object with `.enabled`, `.provider`,
                `.policies_dir`, `.circuit_breaker_threshold`,
                `.circuit_breaker_timeout` — or None to disable outright.
        audit_sink: already-constructed AuditSinkPort (sqlite or null).
    """
    if config is None or not getattr(config, "enabled", False):
        return None

    provider = getattr(config, "provider", "null").lower().strip()

    if provider == "null":
        return None

    if provider == "builtin":
        return PolicyEvaluator(
            policies_dir=getattr(config, "policies_dir", "tpa/governance/policies"),
            audit_sink=audit_sink,
            breaker_threshold=getattr(config, "circuit_breaker_threshold", 3),
            breaker_timeout=getattr(config, "circuit_breaker_timeout", 60.0),
        )

    if provider == "agt":
        raise UnsupportedGovernanceProviderError(
            "governance provider 'agt' requires the external agent-governance-toolkit "
            "package and is not supported in this build"
        )

    raise UnsupportedGovernanceProviderError(f"unknown governance provider: {provider!r}")