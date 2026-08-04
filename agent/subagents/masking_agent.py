"""
agent/subagents/masking_agent.py — Section 2.1's Masking Agent.

Tools it owns: `classify_sensitivity`, `propose_masking_rule`, `apply_masking_dry_run`.

"Identifies sensitive columns..., proposes masking strategy per column,
enforces same-strategy/same-salt across FK-linked columns so referential
integrity survives masking." This subagent is a scoped wrapper around
Phase 2's `core.masking` module — `SensitivityClassifier`, `MaskingEngine`,
and `propagate_fk_rules` were already built and tested there; Phase 4 adds
strategy *proposal* (picking a sensible default strategy per column, not
just applying one a human already chose) plus the dispatch/condensed-
handoff contract shared by every subagent.
"""
from __future__ import annotations

from typing import Any

from agent.subagents.base import Subagent, SubagentResult
from core.database_port import TableMetadata
from core.masking import (
    MaskingEngine,
    MaskingPolicy,
    MaskingRule,
    MaskStrategy,
    SensitivityClassifier,
)


class MaskingAgent(Subagent):
    name = "masking_agent"

    def __init__(self, storage, sensitive_columns: set[str]) -> None:
        super().__init__(storage)
        self._classifier = SensitivityClassifier(sensitive_columns=sensitive_columns)
        self._engine = MaskingEngine()

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "classify_sensitivity":
            return self.classify_sensitivity(task["table"])
        if action == "propose_masking_rule":
            return self.propose_masking_rule(task["table"], task.get("salt_prefix", "auto"))
        if action == "apply_masking_dry_run":
            return self.apply_masking_dry_run(task["rows"], task["policy"], task.get("limit", 10))
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    def classify_sensitivity(self, table: TableMetadata) -> SubagentResult:
        sensitive_cols = self._classifier.classify_table(table)
        return SubagentResult(
            agent=self.name, action="classify_sensitivity", success=True,
            summary={"table": table.name, "sensitive_columns": sensitive_cols, "count": len(sensitive_cols)},
        )

    def propose_masking_rule(self, table: TableMetadata, salt_prefix: str = "auto") -> SubagentResult:
        """Picks a per-column default strategy: DETERMINISTIC for anything
        that looks like an identifier (drives joins/lookups), SYNTHETIC for
        anything that looks like a human name, NULLIFY otherwise — a
        reasonable, inspectable starting policy a human then reviews/edits,
        never applies unreviewed."""
        sensitive_cols = self._classifier.classify_table(table)
        policy = MaskingPolicy(name=f"{table.name}_proposed")
        proposals = []
        for col in sensitive_cols:
            col_lower = col.lower()
            if any(tok in col_lower for tok in ("id", "ssn", "tin", "npi", "dea", "num")):
                strategy = MaskStrategy.DETERMINISTIC
            elif any(tok in col_lower for tok in ("nam", "name")):
                strategy = MaskStrategy.SYNTHETIC
            else:
                strategy = MaskStrategy.DETERMINISTIC
            faker_provider = "name" if strategy == MaskStrategy.SYNTHETIC else None
            rule = MaskingRule(column=col, strategy=strategy, salt=f"{salt_prefix}-{table.name}", faker_provider=faker_provider)
            policy.set_rule(rule)
            proposals.append({"column": col, "strategy": strategy.value, "salt": rule.salt})

        result = self._store_detail("propose_masking_rule", {"policy_name": policy.name, "rules": proposals},
                                     record_count=len(proposals))
        result.summary = {"table": table.name, "rule_count": len(proposals)}
        result.runtime_payload = policy
        return result

    def apply_masking_dry_run(self, rows: list[dict], policy: MaskingPolicy, limit: int = 10) -> SubagentResult:
        preview = self._engine.apply_masking_dry_run(rows, policy, limit=limit)
        result = self._store_detail("apply_masking_dry_run", preview, record_count=len(rows))
        result.summary = {"policy": policy.name, "preview_rows": len(preview), "total_rows": len(rows)}
        return result
