"""
core/database_port.py — Hexagonal "port" every database adapter must implement.

Per Section 3 of the platform spec: a `DatabasePort` interface (introspect,
validate-dialect, generate-DDL/DML, execute, explain-plan) with one adapter
per engine. Adding a database = adding one adapter, zero changes to core
domain logic.

This module defines ONLY the interface and the shared data contracts
(TableMetadata / ColumnMetadata / ForeignKeyMetadata / ExecutionResult).
No adapter-specific (psycopg, oracledb, pyodbc, ...) imports live here —
that is the whole point of the port/adapter split.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


# ─────────────────────────────────────────────────────────────────────────── #
# Shared data contracts (engine-agnostic)                                     #
# ─────────────────────────────────────────────────────────────────────────── #


@dataclass
class ColumnMetadata:
    name: str
    data_type: str                 # native type string as reported by the engine
    nullable: bool = True
    default: Optional[str] = None
    is_primary_key: bool = False
    ordinal_position: Optional[int] = None


@dataclass
class ForeignKeyMetadata:
    fk_name: str
    child_table: str
    child_columns: list[str]
    parent_table: str
    parent_columns: list[str]


@dataclass
class TableMetadata:
    name: str
    schema: str
    columns: list[ColumnMetadata] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyMetadata] = field(default_factory=list)
    required_for_active_status: bool = False


@dataclass
class ExecutionResult:
    """Engine-agnostic result of executing one statement or one batch."""
    success: bool
    rows_affected: int = 0
    error: Optional[str] = None
    statement: Optional[str] = None


@dataclass
class ExplainResult:
    success: bool
    plan_text: str = ""
    estimated_cost: Optional[float] = None
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────── #
# The port itself                                                             #
# ─────────────────────────────────────────────────────────────────────────── #


class DatabasePort(ABC):
    """
    Every connector adapter (Postgres, MySQL, Oracle, SQL Server, ...)
    implements this interface. Nothing in `core/` or the future agent layer
    is allowed to import a driver-specific module directly — only this port.
    """

    engine_name: str = "unknown"
    is_source_only: bool = False   # Section 7a: PROD-tier connections are source-only

    # -- Connection lifecycle -----------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def test_connection(self) -> bool: ...

    # -- Introspection --------------------------------------------------------
    @abstractmethod
    def introspect_schema(self, schema: str) -> list[TableMetadata]:
        """Return structural metadata for every table in `schema`."""

    # -- Dialect / DDL / DML generation --------------------------------------
    @abstractmethod
    def validate_dialect(self, sql: str) -> list[str]:
        """Return a list of dialect-compatibility warnings (empty = clean)."""

    @abstractmethod
    def generate_ddl(self, table: TableMetadata) -> str:
        """Return a CREATE TABLE statement in this engine's dialect."""

    # -- Execution --------------------------------------------------------
    @abstractmethod
    def execute(self, sql: str, params: Optional[tuple] = None) -> ExecutionResult:
        """
        Execute a single statement. Adapters MUST refuse writes when
        `self.is_source_only` is True (Section 7a) and return a failed
        ExecutionResult rather than raising past the port boundary.
        """

    @abstractmethod
    def explain(self, sql: str) -> ExplainResult: ...


class SupportsRollback(Protocol):
    """Optional capability — adapters that can build/execute a rollback plan."""

    def build_rollback_plan(self, executed_statements: list[str]) -> list[str]: ...

    def rollback(self, plan: list[str]) -> ExecutionResult: ...
