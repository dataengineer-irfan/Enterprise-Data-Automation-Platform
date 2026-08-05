"""
adapters/sqlite_adapter.py — SQLite DatabasePort adapter (Phase 7).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from core.database_port import (
    ColumnMetadata,
    DatabasePort,
    ExecutionResult,
    ExplainResult,
    TableMetadata,
)

logger = logging.getLogger(__name__)


class SQLiteAdapter(DatabasePort):
    engine_name = "sqlite"

    def __init__(self, db_config: dict, schema: str = "main", is_source_only: bool = False) -> None:
        self.db_path = db_config.get("database", ":memory:")
        self.schema = schema
        self.is_source_only = is_source_only
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)

    def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def test_connection(self) -> bool:
        try:
            self.connect()
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1")
            return True
        except Exception:
            return False

    def introspect_schema(self, schema: str) -> list[TableMetadata]:
        self.connect()
        cursor = self._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]
        tables = []
        for name in table_names:
            cursor.execute(f"PRAGMA table_info('{name}')")
            cols = []
            pk_cols = []
            for cid, cname, ctype, notnull, dflt, pk in cursor.fetchall():
                is_pk = bool(pk)
                if is_pk:
                    pk_cols.append(cname)
                cols.append(ColumnMetadata(name=cname, data_type=ctype, nullable=not int(notnull), is_primary_key=is_pk))
            tables.append(TableMetadata(name=name, schema=schema, columns=cols, primary_key=pk_cols))
        return tables

    def validate_dialect(self, sql: str) -> list[str]:
        return []

    def generate_ddl(self, table: TableMetadata) -> str:
        cols = ",\n    ".join(
            f"{c.name} {c.data_type.upper()}" + ("" if c.nullable else " NOT NULL")
            for c in table.columns
        )
        pk = f",\n    PRIMARY KEY ({', '.join(table.primary_key)})" if table.primary_key else ""
        return f"CREATE TABLE {table.name} (\n    {cols}{pk}\n);"

    def execute(self, sql: str, params: Optional[tuple] = None) -> ExecutionResult:
        if self.is_source_only:
            return ExecutionResult(success=False, error="Refused: source-only connection", statement=sql)
        self.connect()
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params or ())
            self._conn.commit()
            return ExecutionResult(success=True, rows_affected=cursor.rowcount, statement=sql)
        except Exception as exc:
            return ExecutionResult(success=False, error=str(exc), statement=sql)

    def explain(self, sql: str) -> ExplainResult:
        self.connect()
        try:
            cursor = self._conn.cursor()
            cursor.execute(f"EXPLAIN QUERY PLAN {sql}")
            rows = cursor.fetchall()
            text = "\n".join(str(r) for r in rows)
            return ExplainResult(success=True, plan_text=text)
        except Exception as exc:
            return ExplainResult(success=False, error=str(exc))
