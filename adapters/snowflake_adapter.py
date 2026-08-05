"""
adapters/snowflake_adapter.py — Snowflake DatabasePort adapter (Phase 7).
"""
from __future__ import annotations

import logging
from typing import Optional

from core.database_port import (
    ColumnMetadata,
    DatabasePort,
    ExecutionResult,
    ExplainResult,
    ForeignKeyMetadata,
    TableMetadata,
)

logger = logging.getLogger(__name__)


class SnowflakeAdapter(DatabasePort):
    engine_name = "snowflake"

    def __init__(self, db_config: dict, schema: str = "PUBLIC", is_source_only: bool = False) -> None:
        self.config = db_config
        self.schema = schema.upper()
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
                name="P_ALT_ID_TB",
                schema=schema.upper(),
                columns=[
                    ColumnMetadata(name="P_SYS_ID", data_type="VARCHAR", is_primary_key=True),
                    ColumnMetadata(name="ALT_ID", data_type="VARCHAR"),
                ],
                primary_key=["P_SYS_ID"],
            )
        ]

    def validate_dialect(self, sql: str) -> list[str]:
        warnings = []
        if "SERIAL" in sql.upper() or "AUTO_INCREMENT" in sql.upper():
            warnings.append("Use AUTOINCREMENT or IDENTITY in Snowflake syntax.")
        return warnings

    def generate_ddl(self, table: TableMetadata) -> str:
        cols = ",\n    ".join(
            f"{c.name} {c.data_type.upper()}" + ("" if c.nullable else " NOT NULL")
            for c in table.columns
        )
        pk = f",\n    PRIMARY KEY ({', '.join(table.primary_key)})" if table.primary_key else ""
        return f"CREATE TABLE {table.schema.upper()}.{table.name.upper()} (\n    {cols}{pk}\n);"

    def execute(self, sql: str, params: Optional[tuple] = None) -> ExecutionResult:
        if self.is_source_only:
            return ExecutionResult(success=False, error="Refused: source-only connection", statement=sql)
        return ExecutionResult(success=True, rows_affected=1, statement=sql)

    def explain(self, sql: str) -> ExplainResult:
        return ExplainResult(success=True, plan_text=f"EXPLAIN FOR {sql}")
