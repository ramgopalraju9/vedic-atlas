"""PolicyEvaluator — YAML-based policy engine + circuit breaker.

Donor: veda/governance/builtin.py's BuiltinProvider, read in full and
renamed (there's no "agt"/"builtin" distinction to imply anymore — this
is simply the one real GovernanceProvider implementation). Changes:
  - Takes an injected `AuditSinkPort` instead of constructing a concrete
    `SQLiteAuditBackend`/`NullAuditBackend` itself — the concrete choice
    is made by whoever builds this (service/governance/factory.py),
    keeping this class free of any tpa/SQLite dependency (migration rule 6).
  - Circuit-breaker bookkeeping delegated to `CircuitBreaker` (own module,
    see its docstring) instead of inlined `_BreakerState` handling.
  - Policy YAML directory is still read directly from disk in the
    constructor (matching the donor exactly) — this is a conscious,
    documented exception to "ports before adapters": there is no
    real-world scenario in this project where "load policy rules from
    disk" needs to be swapped for another storage backend, unlike
    conversation/memory persistence, so no PolicyStorePort was created
    for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from domain.entities.audit_entry import AuditEntry
from domain.entities.policy_decision import PolicyDecision
from domain.ports.audit_sink_port import AuditSinkPort
from service.governance.circuit_breaker import CircuitBreaker
from core.logging_config import logger


@dataclass
class PolicyRule:
    """A single policy rule loaded from YAML."""

    name: str
    condition_field: str
    operator: str  # eq, ne, in, not_in, matches, contains, starts_with, glob
    value: Any
    action: str = "deny"  # allow | deny | audit
    priority: int = 0


class PolicyEvaluator:
    """YAML-based policy engine + circuit breaker. Zero external deps beyond PyYAML."""

    def __init__(
        self,
        policies_dir: str | Path,
        audit_sink: AuditSinkPort,
        breaker_threshold: int = 3,
        breaker_timeout: float = 60.0,
    ):
        self._rules: list[PolicyRule] = []
        self._blocked_patterns: list[re.Pattern] = []
        self._audit = audit_sink
        self._breaker = CircuitBreaker(threshold=breaker_threshold, timeout_sec=breaker_timeout)
        self._load_policies(Path(policies_dir))

    @property
    def name(self) -> str:
        return "builtin"

    def _load_policies(self, policies_dir: Path) -> None:
        if not policies_dir.exists():
            logger.info(f"[governance] no policies dir at {policies_dir}")
            return

        for f in sorted(policies_dir.glob("*.yaml")) + sorted(policies_dir.glob("*.yml")):
            try:
                with open(f) as fh:
                    doc = yaml.safe_load(fh) or {}
                rules_data = doc.get("rules", [])
                for r in rules_data:
                    cond = r.get("condition", {})
                    self._rules.append(
                        PolicyRule(
                            name=r.get("name", f.stem),
                            condition_field=cond.get("field", ""),
                            operator=cond.get("operator", "eq"),
                            value=cond.get("value"),
                            action=r.get("action", "deny"),
                            priority=r.get("priority", 0),
                        )
                    )
                for p in doc.get("blocked_patterns", []):
                    self._blocked_patterns.append(re.compile(p, re.IGNORECASE))

                logger.info(f"[governance] loaded {len(rules_data)} rules from {f.name}")
            except Exception as e:
                logger.warning(f"[governance] failed to load {f}: {e}")

        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def check_action(self, action: str, context: dict) -> PolicyDecision:
        for rule in self._rules:
            ctx_value = context.get(rule.condition_field, action if rule.condition_field == "action" else None)
            if ctx_value is None:
                continue

            matched = False
            op = rule.operator
            if op == "eq":
                matched = ctx_value == rule.value
            elif op == "ne":
                matched = ctx_value != rule.value
            elif op == "in":
                matched = ctx_value in (rule.value or [])
            elif op == "not_in":
                matched = ctx_value not in (rule.value or [])
            elif op == "contains":
                matched = str(rule.value) in str(ctx_value)
            elif op == "starts_with":
                matched = str(ctx_value).startswith(str(rule.value))
            elif op == "matches":
                matched = bool(re.search(str(rule.value), str(ctx_value)))
            elif op == "glob":
                import fnmatch
                matched = fnmatch.fnmatch(str(ctx_value), str(rule.value))

            if matched:
                allowed = rule.action in ("allow", "audit")
                return PolicyDecision(
                    allowed=allowed,
                    action=rule.action,
                    rule_name=rule.name,
                    reason=f"Rule '{rule.name}' matched: {rule.condition_field} {rule.operator} {rule.value}",
                )

        return PolicyDecision()  # default: allowed

    def check_pattern(self, text: str) -> list[str]:
        matches = []
        for pattern in self._blocked_patterns:
            found = pattern.findall(text)
            if found:
                matches.extend(found)
        return matches

    async def audit(self, entry: AuditEntry) -> None:
        self._audit.record(entry)

    def is_healthy(self, backend: str) -> bool:
        return self._breaker.is_healthy(backend)

    def record_success(self, backend: str) -> None:
        self._breaker.record_success(backend)

    def record_failure(self, backend: str) -> None:
        self._breaker.record_failure(backend)

    @property
    def audit_backend(self) -> AuditSinkPort:
        return self._audit

    @property
    def rules_count(self) -> int:
        return len(self._rules)

    @property
    def breaker_states(self) -> dict[str, dict]:
        return self._breaker.states