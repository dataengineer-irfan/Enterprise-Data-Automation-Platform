"""
sql_executor.py — Execute converted PostgreSQL DDL statements.

Responsibilities
────────────────
1. Create the target schema (and any required extensions) if they don't exist.
2. Execute the per-table DDL statements (DROP + CREATE TABLE + indexes +
   comments) one statement at a time with autocommit so a single failure
   never rolls back other successful statements.
3. Execute the batch of deferred FK ALTER TABLE statements at the end,
   collecting per-FK success/failure details.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from db import DatabaseManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────── #
# Result types                                                                 #
# ─────────────────────────────────────────────────────────────────────────── #


@dataclass
class ExecutionResult:
    """Outcome of executing DDL for one table."""
    table_name          : str
    success             : bool
    statements_executed : int = 0
    error               : Optional[str] = None


@dataclass
class FKResult:
    """Outcome of executing one FK ALTER TABLE statement."""
    sql    : str
    success: bool
    error  : Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────── #
# SQLExecutor                                                                  #
# ─────────────────────────────────────────────────────────────────────────── #


class SQLExecutor:
    """
    Executes PostgreSQL DDL statements via a :class:`DatabaseManager`.

    All statements run with ``autocommit=True`` so each one is immediately
    committed and a failure in one statement never aborts another.

    Parameters
    ----------
    db_manager : DatabaseManager
        An open (or auto-opening) database connection manager.
    schema     : str
        Name of the target PostgreSQL schema (e.g. ``"provider"``).
    """

    def __init__(self, db_manager: DatabaseManager, schema: str) -> None:
        self._db     = db_manager
        self._schema = schema

    # ------------------------------------------------------------------ #
    # Schema bootstrap                                                     #
    # ------------------------------------------------------------------ #

    def ensure_schema(self) -> None:
        """
        Create the target schema if it does not already exist.
        Also enables the ``pgcrypto`` extension used by ``gen_random_uuid()``.
        """
        conn = self._db.connect()
        with conn.cursor() as cur:
            # pgcrypto supplies gen_random_uuid() for SYS_GUID() defaults
            cur.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema};")
        logger.info("Schema '%s' is ready.", self._schema)

    # ------------------------------------------------------------------ #
    # Per-table DDL execution                                              #
    # ------------------------------------------------------------------ #

    def execute_table_ddl(
        self,
        table_name: str,
        statements: list[str],
    ) -> ExecutionResult:
        """
        Execute all DDL statements for a single table.

        Execution stops on the first error to avoid cascading failures
        (e.g., trying to create indexes on a table that failed to create).

        Parameters
        ----------
        table_name : str
            Used only for logging / result reporting.
        statements : list[str]
            Individual SQL statements (one statement per list item,
            each may include a trailing semicolon).
        """
        result = ExecutionResult(table_name=table_name, success=True)
        conn   = self._db.connect()

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                result.statements_executed += 1
                logger.debug("OK  %s …", stmt[:80].replace("\n", " "))
            except Exception as exc:  # noqa: BLE001
                result.success = False
                result.error   = str(exc)
                logger.error(
                    "FAIL [%s] %s | stmt: %s",
                    table_name,
                    exc,
                    stmt[:120].replace("\n", " "),
                )
                break   # Stop processing this table on first error

        return result

    # ------------------------------------------------------------------ #
    # Deferred FK execution                                                #
    # ------------------------------------------------------------------ #

    def execute_fk_statements(
        self,
        fk_statements: list[str],
    ) -> list[FKResult]:
        """
        Execute every FK ``ALTER TABLE … ADD CONSTRAINT`` statement.

        Each statement is attempted independently; failures are logged but
        do not prevent the remaining FKs from being tried.

        Returns
        -------
        list[FKResult]
            One :class:`FKResult` per non-empty input statement.
        """
        results: list[FKResult] = []
        conn = self._db.connect()

        for fk_sql in fk_statements:
            fk_sql = fk_sql.strip()
            if not fk_sql:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(fk_sql)
                results.append(FKResult(sql=fk_sql, success=True))
                logger.debug("FK OK  %s …", fk_sql[:80].replace("\n", " "))
            except Exception as exc:  # noqa: BLE001
                results.append(FKResult(sql=fk_sql, success=False, error=str(exc)))
                logger.error(
                    "FK FAIL  %s | stmt: %s",
                    exc,
                    fk_sql[:120].replace("\n", " "),
                )

        return results
