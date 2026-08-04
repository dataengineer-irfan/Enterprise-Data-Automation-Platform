"""
adapters/oracle_ddl_adapter.py — Oracle DatabasePort adapter, DDL-file mode.

Phase-1 scope: no live Oracle instance is required for dev/test (per the
platform spec's Section 8 Oracle guidance: "Always Free" tier or Dockerized
Oracle XE — neither is available in this sandbox). Instead this adapter
introspects structure straight from the real Oracle DDL export already in
`input/ddl/*.sql`, and reconciles PK/FK against `relationships_verified.yaml`
(the project's confirmed ground truth) rather than trusting per-file FK
parsing, since a handful of files only carry the FK on one side of a
composite/self-referencing relationship.

`execute()` / `explain()` intentionally return a clean failure — this
adapter is introspection-only until a live `python-oracledb` connection is
wired in a later phase (per Section 3, that's a drop-in second adapter
method set, not a rewrite of this file).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ddl_converter import DDLConverter

from core.database_port import (
    ColumnMetadata,
    DatabasePort,
    ExecutionResult,
    ExplainResult,
    ForeignKeyMetadata,
    TableMetadata,
)
from core.schema_graph import SchemaGraph

logger = logging.getLogger(__name__)

_COLUMN_LINE = re.compile(
    r"^\s{4}([a-z0-9_]+)\s+([A-Za-z0-9_() ,]+?)(\s+DEFAULT\s+.+?)?(\s+NOT NULL)?,?\s*$"
)


class OracleDDLAdapter(DatabasePort):
    engine_name = "oracle"
    is_source_only = True  # PROD Oracle is read-only for this adapter by design

    def __init__(self, ddl_dir: Path, schema_graph: SchemaGraph, pg_schema: str = "provider") -> None:
        self._ddl_dir = ddl_dir
        self._graph = schema_graph
        self._converter = DDLConverter(schema=pg_schema)
        self._connected = False

    # -- connection lifecycle (no-op: file-based introspection) -------------
    def connect(self) -> None:
        if not self._ddl_dir.exists():
            raise FileNotFoundError(f"DDL directory not found: {self._ddl_dir}")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def test_connection(self) -> bool:
        try:
            self.connect()
            return any(self._ddl_dir.glob("*.sql"))
        except Exception as exc:  # noqa: BLE001
            logger.error("Oracle DDL adapter self-check failed: %s", exc)
            return False

    # -- introspection --------------------------------------------------------
    def introspect_schema(self, schema: str) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for ddl_file in sorted(self._ddl_dir.glob("*.sql")):
            conversion = self._converter.convert_file(ddl_file)
            if not conversion.success or not conversion.pg_statements:
                logger.warning(
                    "Skipping %s during introspection: %s", ddl_file.name, conversion.error
                )
                continue

            columns = self._parse_columns(conversion.pg_statements)
            node = self._graph.tables.get(conversion.table_name.upper())

            # relationships_verified.yaml stores column names in Oracle's
            # native UPPER_CASE; the DDL-converted columns above (and every
            # CSV/row-dict this metadata gets matched against downstream)
            # are lower_case. Normalize PK/FK column names to lower_case
            # here, once, at the adapter boundary — every consumer
            # (ValidationEngine, MaskingEngine, CLI) then only ever sees one
            # casing convention and doesn't need its own workaround.
            fks = (
                [
                    ForeignKeyMetadata(
                        fk_name=fk.fk_name,
                        child_table=fk.child_table,
                        child_columns=[c.lower() for c in fk.child_columns],
                        parent_table=fk.parent_table,
                        parent_columns=[c.lower() for c in fk.parent_columns],
                    )
                    for fk in node.foreign_keys
                ]
                if node
                else []
            )
            pk = [c.lower() for c in node.primary_key] if node else []
            for col in columns:
                col.is_primary_key = col.name.lower() in pk

            tables.append(
                TableMetadata(
                    name=conversion.table_name,
                    schema=schema,
                    columns=columns,
                    primary_key=pk,
                    foreign_keys=fks,
                    required_for_active_status=node.required_for_active_status if node else False,
                )
            )
        return tables

    @staticmethod
    def _parse_columns(pg_statements: list[str]) -> list[ColumnMetadata]:
        columns: list[ColumnMetadata] = []
        for stmt in pg_statements:
            if "CREATE TABLE" not in stmt.upper():
                continue
            for i, raw_line in enumerate(stmt.splitlines()):
                line = raw_line.rstrip(",").strip()
                if not line or line.upper().startswith(("CONSTRAINT", "CREATE", "DROP", "SET")):
                    continue
                m = _COLUMN_LINE.match(raw_line)
                if not m:
                    continue
                name, dtype, default, not_null = m.groups()
                columns.append(
                    ColumnMetadata(
                        name=name,
                        data_type=dtype.strip(),
                        nullable=not_null is None,
                        default=(default or "").replace("DEFAULT", "").strip() or None,
                        ordinal_position=i,
                    )
                )
        return columns

    # -- dialect / DDL --------------------------------------------------------
    def validate_dialect(self, sql: str) -> list[str]:
        warnings = []
        for token in ("VARCHAR2", "SYSDATE", "NUMBER(", "SYS_GUID"):
            if token in sql.upper():
                warnings.append(f"'{token}' is Oracle-specific; not portable as written.")
        return warnings

    def generate_ddl(self, table: TableMetadata) -> str:
        raise NotImplementedError(
            "OracleDDLAdapter is introspection-only in Phase 1; use PostgresAdapter "
            "to generate target DDL from the converted (already-Postgres-dialect) statements."
        )

    # -- execution (refused by design; see class docstring) -------------------
    def execute(self, sql: str, params: Optional[tuple] = None) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            error="OracleDDLAdapter is source-only / introspection-only in Phase 1.",
            statement=sql,
        )

    def explain(self, sql: str) -> ExplainResult:
        return ExplainResult(success=False, error="Not supported without a live Oracle connection.")
