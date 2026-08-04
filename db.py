"""
db.py — PostgreSQL connection manager (psycopg v3).

Provides a single persistent connection with autocommit=True so that each
DDL statement is immediately visible.  Use the context-manager `cursor()`
for safe cursor lifecycle management.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

import psycopg

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages a single persistent PostgreSQL connection.

    Usage
    -----
    db = DatabaseManager(DB_CONFIG)
    db.connect()
    db.execute("SELECT 1")
    db.disconnect()
    """

    def __init__(self, db_config: dict[str, Any]) -> None:
        self._config = db_config
        self._conn: psycopg.Connection | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> psycopg.Connection:
        """Return the active connection, creating one if necessary."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(**self._config)
            self._conn.autocommit = True
            logger.info(
                "Connected to PostgreSQL  host=%s  port=%s  db=%s",
                self._config.get("host"),
                self._config.get("port"),
                self._config.get("dbname"),
            )
        return self._conn

    def disconnect(self) -> None:
        """Close the connection if it is open."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None
            logger.info("Disconnected from PostgreSQL.")

    # ------------------------------------------------------------------
    # Cursor context manager
    # ------------------------------------------------------------------

    @contextmanager
    def cursor(self) -> Generator[psycopg.Cursor, None, None]:  # type: ignore[type-arg]
        """Yield a cursor and guarantee it is closed afterwards."""
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Statement execution
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: tuple | None = None) -> None:
        """Execute a single SQL statement (DDL or DML)."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)

    def test_connection(self) -> bool:
        """Return True when the database is reachable."""
        try:
            self.execute("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Connection test failed: %s", exc)
            return False
