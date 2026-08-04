"""
adapters/postgres_adapter.py — PostgreSQL DatabasePort adapter.

Wraps the existing, working `DatabaseManager` (psycopg v3, db.py),
`SQLExecutor` (sql_executor.py) and `DDLConverter` (ddl_converter.py) from
the reused converter project behind the `DatabasePort` interface (Section 3),
so the rest of the platform never imports psycopg directly.

Section 7a enforcement lives here, not just in the UI: a connection marked
`is_source_only=True` refuses every write at the connector layer.
"""
from __future__ import annotations

import logging
from typing import Optional

from db import DatabaseManager
from sql_executor import SQLExecutor

from core.database_port import (
    ColumnMetadata,
    DatabasePort,
    ExecutionResult,
    ExplainResult,
    ForeignKeyMetadata,
    TableMetadata,
)

logger = logging.getLogger(__name__)

_INTROSPECT_COLUMNS_SQL = """
    SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
           c.column_default, c.ordinal_position
    FROM information_schema.columns c
    WHERE c.table_schema = %s
    ORDER BY c.table_name, c.ordinal_position;
"""

_INTROSPECT_PK_SQL = """
    SELECT tc.table_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %s
    ORDER BY tc.table_name, kcu.ordinal_position;
"""

_INTROSPECT_FK_SQL = """
    SELECT
        tc.constraint_name, tc.table_name AS child_table, kcu.column_name AS child_column,
        ccu.table_name AS parent_table, ccu.column_name AS parent_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s
    ORDER BY tc.table_name;
"""


class PostgresAdapter(DatabasePort):
    engine_name = "postgres"

    def __init__(self, db_config: dict, schema: str = "provider", is_source_only: bool = False) -> None:
        self._manager = DatabaseManager(db_config)
        self._executor = SQLExecutor(self._manager, schema)
        self._schema = schema
        self.is_source_only = is_source_only

    # -- connection lifecycle -------------------------------------------------
    def connect(self) -> None:
        self._manager.connect()

    def disconnect(self) -> None:
        self._manager.disconnect()

    def test_connection(self) -> bool:
        return self._manager.test_connection()

    # -- introspection --------------------------------------------------------
    def introspect_schema(self, schema: str) -> list[TableMetadata]:
        tables: dict[str, TableMetadata] = {}

        with self._manager.cursor() as cur:
            cur.execute(_INTROSPECT_COLUMNS_SQL, (schema,))
            for table_name, col_name, data_type, is_nullable, default, ordinal in cur.fetchall():
                t = tables.setdefault(table_name, TableMetadata(name=table_name, schema=schema))
                t.columns.append(
                    ColumnMetadata(
                        name=col_name,
                        data_type=data_type,
                        nullable=(is_nullable == "YES"),
                        default=default,
                        ordinal_position=ordinal,
                    )
                )

            cur.execute(_INTROSPECT_PK_SQL, (schema,))
            for table_name, col_name in cur.fetchall():
                if table_name in tables:
                    tables[table_name].primary_key.append(col_name)
                    for c in tables[table_name].columns:
                        if c.name == col_name:
                            c.is_primary_key = True

            cur.execute(_INTROSPECT_FK_SQL, (schema,))
            for fk_name, child_table, child_col, parent_table, parent_col in cur.fetchall():
                if child_table not in tables:
                    continue
                existing = next(
                    (fk for fk in tables[child_table].foreign_keys if fk.fk_name == fk_name), None
                )
                if existing:
                    existing.child_columns.append(child_col)
                    existing.parent_columns.append(parent_col)
                else:
                    tables[child_table].foreign_keys.append(
                        ForeignKeyMetadata(
                            fk_name=fk_name,
                            child_table=child_table,
                            child_columns=[child_col],
                            parent_table=parent_table,
                            parent_columns=[parent_col],
                        )
                    )
        return list(tables.values())

    # -- dialect / DDL --------------------------------------------------------
    def validate_dialect(self, sql: str) -> list[str]:
        warnings = []
        for token in ("VARCHAR2", "SYSDATE", "NUMBER(", "SYS_GUID", "NVL(", "ROWNUM"):
            if token in sql.upper():
                warnings.append(f"'{token}' is not valid PostgreSQL syntax.")
        return warnings

    def generate_ddl(self, table: TableMetadata) -> str:
        cols = ",\n    ".join(f"{c.name} {c.data_type}" + ("" if c.nullable else " NOT NULL") for c in table.columns)
        pk = f",\n    PRIMARY KEY ({', '.join(table.primary_key)})" if table.primary_key else ""
        return f"CREATE TABLE {table.schema}.{table.name} (\n    {cols}{pk}\n);"

    # -- execution --------------------------------------------------------
    def execute(self, sql: str, params: Optional[tuple] = None) -> ExecutionResult:
        if self.is_source_only:
            return ExecutionResult(
                success=False,
                error="Refused: this connection is registered source-only (Section 7a). "
                "Writes must target a connection with role=target.",
                statement=sql,
            )
        try:
            self._manager.execute(sql, params)
            return ExecutionResult(success=True, statement=sql)
        except Exception as exc:  # noqa: BLE001
            logger.error("Execution failed: %s", exc)
            return ExecutionResult(success=False, error=str(exc), statement=sql)

    def explain(self, sql: str) -> ExplainResult:
        if self.is_source_only:
            pass  # EXPLAIN is read-only; allowed even on source-only connections
        try:
            with self._manager.cursor() as cur:
                cur.execute(f"EXPLAIN {sql}")
                plan_lines = [row[0] for row in cur.fetchall()]
                return ExplainResult(success=True, plan_text="\n".join(plan_lines))
        except Exception as exc:  # noqa: BLE001
            return ExplainResult(success=False, error=str(exc))

    # -- schema bootstrap (delegates to the existing, tested SQLExecutor) -----
    def ensure_schema(self) -> None:
        self._executor.ensure_schema()

    def run_table_ddl(self, table_name: str, statements: list[str]):
        return self._executor.execute_table_ddl(table_name, statements)

    def run_fk_batch(self, fk_statements: list[str]):
        return self._executor.execute_fk_statements(fk_statements)
