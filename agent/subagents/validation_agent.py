"""
agent/subagents/validation_agent.py — Section 2.1's Validation Agent.

Tools it owns: `validate_batch`, `explain_violation`.

"Runs the full validation engine (Section 4) against proposed data; never
fixes anything itself — only reports." This subagent is a scoped wrapper
around `core.validation.ValidationEngine` (Phase 2) — the engine itself
was already built and tested; Phase 4 adds the dispatch contract and the
condensed-handoff behavior (a 10k-row report becomes a summary + a
shared-storage pointer, not a context dump, per Section 2.2 rule #5).
"""
from __future__ import annotations

from typing import Any

from agent.subagents.base import Subagent, SubagentResult
from core.database_port import TableMetadata
from core.glossary import Glossary
from core.schema_graph import SchemaGraph
from core.validation import ValidationContext, ValidationEngine, ValidationIssue


class ValidationAgent(Subagent):
    name = "validation_agent"

    def __init__(self, storage, graph: SchemaGraph, glossary: Glossary) -> None:
        super().__init__(storage)
        self._graph = graph
        self._glossary = glossary

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "validate_batch":
            return self.validate_batch(task["rows"], task["table"], task.get("existing_keys", {}))
        if action == "explain_violation":
            return self.explain_violation(task["issue"])
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    def validate_batch(self, rows: list[dict], table: TableMetadata, existing_keys: dict[str, set] | None = None) -> SubagentResult:
        engine = ValidationEngine(ValidationEngine.default_rules_for(table))
        context = ValidationContext(schema_graph=self._graph, glossary=self._glossary, existing_keys=existing_keys or {})
        report = engine.validate_batch(rows, table, context)

        detail = {
            "issues": [
                {"rule": i.rule, "severity": i.severity.value, "row": i.row_index, "column": i.column, "message": i.message}
                for i in report.issues
            ]
        }
        result = self._store_detail("validate_batch", detail, record_count=len(report.issues))
        result.summary = report.summary()
        return result

    def explain_violation(self, issue: dict) -> SubagentResult:
        """Turns one structured issue into a human-readable one-liner —
        deliberately templated, not LLM-backed, so it stays instant and
        deterministic for the (common) case of explaining hundreds of
        issues in a report."""
        explanation = (
            f"Row {issue.get('row')}, column '{issue.get('column')}': {issue.get('message')} "
            f"(rule: {issue.get('rule')}, severity: {issue.get('severity')})"
        )
        return SubagentResult(agent=self.name, action="explain_violation", success=True, summary={"explanation": explanation})
