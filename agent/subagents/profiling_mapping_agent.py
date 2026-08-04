"""
agent/subagents/profiling_mapping_agent.py — Section 2.1's Profiling/Mapping Agent.

Tools it owns: `profile_file`, `suggest_column_mapping`, `diff_against_schema`.

Column-mapping is a fuzzy problem (an uploaded CSV's `ssn`, `SSN_NUM`, or
`social_security_number` should all suggest `p_alt_id` for a Provider-SSN
table), but it's deliberately implemented here with stdlib `difflib` name
similarity rather than an LLM call: it's fast, fully deterministic, and
testable without a live model — consistent with this codebase's running
choice (ADR-0003) to keep the LLM out of the data path. A genuinely
ambiguous mapping (low confidence on every candidate) is surfaced as a
LOW-confidence suggestion for a human to confirm, not silently guessed —
this subagent proposes, it never applies.
"""
from __future__ import annotations

import csv
import difflib
from pathlib import Path
from typing import Any

from agent.subagents.base import Subagent, SubagentResult
from core.database_port import TableMetadata

_CONFIDENCE_HIGH = 0.75
_CONFIDENCE_LOW = 0.4


def _normalize(name: str) -> str:
    return name.lower().replace("_", "").replace(" ", "")


class ProfilingMappingAgent(Subagent):
    name = "profiling_mapping_agent"

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "profile_file":
            return self.profile_file(Path(task["path"]))
        if action == "suggest_column_mapping":
            return self.suggest_column_mapping(task["csv_columns"], task["table"])
        if action == "diff_against_schema":
            return self.diff_against_schema(task["profile"], task["table"])
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    # -- profile_file --------------------------------------------------------
    def profile_file(self, path: Path) -> SubagentResult:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            return SubagentResult(agent=self.name, action="profile_file", success=False, error="File has no data rows.")

        columns = list(rows[0].keys())
        profile = {"row_count": len(rows), "columns": {}}
        for col in columns:
            values = [r.get(col) for r in rows]
            non_null = [v for v in values if v not in (None, "")]
            profile["columns"][col] = {
                "null_count": len(values) - len(non_null),
                "distinct_count": len(set(non_null)),
                "sample_values": non_null[:3],
                "inferred_type": self._infer_type(non_null),
            }

        result = self._store_detail("profile_file", profile, record_count=len(rows))
        result.summary = {"path": str(path), "row_count": len(rows), "column_count": len(columns)}
        return result

    @staticmethod
    def _infer_type(values: list[str]) -> str:
        if not values:
            return "unknown"
        if all(v.replace("-", "").isdigit() for v in values):
            return "numeric" if all(v.isdigit() for v in values) else "date_or_id"
        return "text"

    # -- suggest_column_mapping --------------------------------------------------------
    def suggest_column_mapping(self, csv_columns: list[str], table: TableMetadata) -> SubagentResult:
        table_columns = [c.name for c in table.columns]
        suggestions = []
        for csv_col in csv_columns:
            norm_csv = _normalize(csv_col)
            scored = sorted(
                ((tc, difflib.SequenceMatcher(None, norm_csv, _normalize(tc)).ratio()) for tc in table_columns),
                key=lambda x: x[1], reverse=True,
            )
            best_col, best_score = scored[0] if scored else (None, 0.0)
            confidence = "high" if best_score >= _CONFIDENCE_HIGH else ("low" if best_score >= _CONFIDENCE_LOW else "none")
            suggestions.append({
                "csv_column": csv_col, "suggested_column": best_col if confidence != "none" else None,
                "score": round(best_score, 3), "confidence": confidence,
            })

        result = self._store_detail("suggest_column_mapping", suggestions, record_count=len(suggestions))
        result.summary = {
            "table": table.name,
            "mapped_high_confidence": sum(1 for s in suggestions if s["confidence"] == "high"),
            "mapped_low_confidence": sum(1 for s in suggestions if s["confidence"] == "low"),
            "unmapped": sum(1 for s in suggestions if s["confidence"] == "none"),
        }
        return result

    # -- diff_against_schema --------------------------------------------------------
    def diff_against_schema(self, profile: dict, table: TableMetadata) -> SubagentResult:
        profiled_cols = set(profile.get("columns", {}).keys())
        table_cols = {c.name for c in table.columns}
        required_cols = {c.name for c in table.columns if not c.nullable}

        diff = {
            "extra_in_file": sorted(profiled_cols - table_cols),
            "missing_from_file": sorted(table_cols - profiled_cols),
            "missing_required": sorted(required_cols - profiled_cols),
        }
        result = self._store_detail("diff_against_schema", diff,
                                     record_count=len(diff["extra_in_file"]) + len(diff["missing_from_file"]))
        result.summary = {
            "table": table.name,
            "extra_count": len(diff["extra_in_file"]),
            "missing_count": len(diff["missing_from_file"]),
            "missing_required_count": len(diff["missing_required"]),
            "clean": not any(diff.values()),
        }
        return result
