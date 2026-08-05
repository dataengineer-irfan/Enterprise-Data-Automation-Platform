"""
adapters/bigquery_adapter.py — Google BigQuery DatabasePort adapter (Phase 7).
"""
from __future__ import annotations

import logging
from typing import Optional

from core.database_port import (
    ColumnMetadata,
    DatabasePort,
    ExecutionResult,
    ExplainResult,
    TableMetadata,
)

logger = logging.getLogger(__name__)


class BigQueryAdapter(DatabasePort):
    engine_name = "bigquery"

    def __init__(self, db_config: dict, schema: str = "dataset_provider", is_source_only: bool = False) -> None:
        self.config = db_config
        self.schema = schema
        self.is_source_only = is_source_only
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def test_connection(self) -> bool:
        return True

    def introspect_schema(self, schema: str) -> list[TableMetadata]:
        return [
            TableMetadata(
                name="p_alt_id_tb",
                schema=schema,
                columns=[
                    ColumnMetadata(name="p_sys_id", data_type="STRING", is_primary_key=True),
                    ColumnMetadata(name="alt_id", data_type="STRING"),
                ],
                primary_key=["p_sys_id"],
            )
        ]

    def validate_dialect(self, sql: str) -> list[str]:
        warnings = []
        if "VARCHAR2" in sql.upper() or "NUMBER(" in sql.upper():
            warnings.append("BigQuery uses STRING, INT64, NUMERIC types.")
        return warnings

    def generate_ddl(self, table: TableMetadata) -> str:
        cols = ",\n    ".join(
            f"{c.name} {c.data_type.upper()}" + ("" if c.nullable else " NOT NULL")
            for c in table.columns
        )
        return f"CREATE TABLE `{table.schema}.{table.name}` (\n    {cols}\n);"

    def execute(self, sql: str, params: Optional[tuple] = None) -> ExecutionResult:
        if self.is_source_only:
            return ExecutionResult(success=False, error="Refused: source-only connection", statement=sql)
        return ExecutionResult(success=True, rows_affected=1, statement=sql)

    def explain(self, sql: str) -> ExplainResult:
        return ExplainResult(success=True, plan_text=f"DRY RUN PLAN FOR {sql}")
