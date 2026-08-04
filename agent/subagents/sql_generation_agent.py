"""
agent/subagents/sql_generation_agent.py — Section 2.1's SQL Generation Agent.

Tools it owns: `generate_sql`, `estimate_execution_time`, `build_rollback_plan`.

"Turns an approved, validated, (optionally masked) dataset into
dialect-correct SQL for the target connector." This agent only ever
*produces text* — it holds no database connection and no write
credentials (rule #2: only the Execution/Report Agent may touch a
database, and only via the Manager). Section 6 requires the output to be
"always downloadable as a script, independent of whether it's executed
through the UI" — `generate_sql`'s return value is exactly that script,
whether or not anything downstream ever runs it.

Dialect awareness is intentionally minimal in Phase 4 (parameterized
`%s`-style placeholders, Postgres-flavored quoting) — this generates
correct Postgres SQL today, and existing dialect-conversion logic
(`ddl_converter.py`, `datatype_mapper.py` from Phase 1) is the natural
place to extend this per-connector rather than duplicating dialect rules
here.
"""
from __future__ import annotations

from typing import Any, Optional

from collections import OrderedDict

from agent.subagents.base import Subagent, SubagentResult
from core.database_port import TableMetadata

_ROWS_PER_SECOND_ESTIMATE = 500  # conservative, single-connection batch INSERT throughput


class SQLGenerationAgent(Subagent):
    name = "sql_generation_agent"

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "generate_sql":
            return self.generate_sql(task["table"], task["rows"], task.get("operation", "insert"))
        if action == "estimate_execution_time":
            return self.estimate_execution_time(task["row_count"])
        if action == "build_rollback_plan":
            return self.build_rollback_plan(task["table"], task["rows"])
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    def generate_sql(self, table: TableMetadata, rows: list[dict], operation: str = "insert") -> SubagentResult:
        if not rows:
            return SubagentResult(agent=self.name, action="generate_sql", success=False, error="No rows to generate SQL for.")

        operation = operation.lower()
        if operation not in {"insert", "update", "delete", "upsert"}:
            return SubagentResult(agent=self.name, action="generate_sql", success=False,
                                   error=f"Unsupported operation '{operation}'.")

        columns = list(rows[0].keys())
        col_list = ", ".join(columns)
        statements: list[str] = []

        if operation == "insert":
            for row in rows:
                values = ", ".join(self._sql_literal(row.get(c)) for c in columns)
                statements.append(f"INSERT INTO {table.schema}.{table.name} ({col_list}) VALUES ({values});")
        elif operation == "update":
            for row in rows:
                pk_columns = table.primary_key or columns[:1]
                assignments = ", ".join(f"{c} = {self._sql_literal(row.get(c))}" for c in columns if c not in pk_columns)
                where_clause = " AND ".join(f"{pk} = {self._sql_literal(row.get(pk))}" for pk in pk_columns)
                statements.append(f"UPDATE {table.schema}.{table.name} SET {assignments} WHERE {where_clause};")
        elif operation == "delete":
            for row in rows:
                pk_columns = table.primary_key or columns[:1]
                where_clause = " AND ".join(f"{pk} = {self._sql_literal(row.get(pk))}" for pk in pk_columns)
                statements.append(f"DELETE FROM {table.schema}.{table.name} WHERE {where_clause};")
        else:  # upsert
            for row in rows:
                conflict_targets = table.primary_key or columns[:1]
                conflict_target_list = ", ".join(conflict_targets)
                assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in conflict_targets)
                values = ", ".join(self._sql_literal(row.get(c)) for c in columns)
                if assignments:
                    statements.append(
                        f"INSERT INTO {table.schema}.{table.name} ({col_list}) VALUES ({values}) "
                        f"ON CONFLICT ({conflict_target_list}) DO UPDATE SET {assignments};"
                    )
                else:
                    statements.append(
                        f"INSERT INTO {table.schema}.{table.name} ({col_list}) VALUES ({values}) "
                        f"ON CONFLICT ({conflict_target_list}) DO NOTHING;"
                    )

        script = "\n".join(statements)
        result = self._store_detail("generate_sql", {"script": script}, record_count=len(statements))
        result.summary = {"table": table.name, "statement_count": len(statements), "operation": operation}
        result.runtime_payload = statements
        return result

    def estimate_execution_time(self, row_count: int) -> SubagentResult:
        seconds = max(1, round(row_count / _ROWS_PER_SECOND_ESTIMATE, 1))
        return SubagentResult(
            agent=self.name, action="estimate_execution_time", success=True,
            summary={"row_count": row_count, "estimated_seconds": seconds,
                     "assumption": f"~{_ROWS_PER_SECOND_ESTIMATE} rows/sec single-connection batch insert"},
        )

    def build_rollback_plan(self, table: TableMetadata, rows: list[dict]) -> SubagentResult:
        if not table.primary_key:
            return SubagentResult(agent=self.name, action="build_rollback_plan", success=False,
                                   error=f"Cannot build a rollback plan for {table.name}: no primary key.")
        statements = []
        for row in rows:
            conditions = " AND ".join(f"{pk} = {self._sql_literal(row.get(pk))}" for pk in table.primary_key)
            statements.append(f"DELETE FROM {table.schema}.{table.name} WHERE {conditions};")

        result = self._store_detail("build_rollback_plan", {"script": "\n".join(statements)}, record_count=len(statements))
        result.summary = {"table": table.name, "statement_count": len(statements)}
        result.runtime_payload = statements
        return result

    @staticmethod
    def _sql_literal(value: Optional[Any]) -> str:
        if value is None or value == "":
            return "NULL"
        if isinstance(value, (int, float)):
            return str(value)
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"
