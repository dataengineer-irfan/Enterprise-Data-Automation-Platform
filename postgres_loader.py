"""
postgres_loader.py — Bulk-load generated CSV files into PostgreSQL.

For each table:
  1. Reads the generated CSV from generated_data/<TABLE>.csv
  2. Truncates the target table (TRUNCATE ... CASCADE) or uses
     ON CONFLICT DO NOTHING if a PK is present.
  3. Inserts all rows in one executemany batch.
  4. Logs per-table row counts and any errors.

Tables are loaded in the same FK-safe order used during generation.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from data_loader import _format_datetime_for_pg
from db import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class LoadResult:
    """Outcome of loading one generated CSV into PostgreSQL."""
    table_name  : str
    rows_loaded : int
    success     : bool
    error       : Optional[str] = None


class PostgresLoader:
    """
    Load generated CSV files into PostgreSQL.

    Parameters
    ----------
    db_manager  : open DatabaseManager
    schema      : PostgreSQL schema name
    data_dir    : directory containing generated CSV files
    table_order : tables in FK-safe order (parents first)
    truncate    : if True, TRUNCATE each table before loading
    """

    def __init__(
        self,
        db_manager  : DatabaseManager,
        schema      : str,
        data_dir    : Path,
        table_order : list[str],
        truncate    : bool = True,
    ) -> None:
        self._db      = db_manager
        self._schema  = schema
        self._dir     = data_dir
        self._order   = table_order
        self._truncate = truncate

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load_all(self) -> list[LoadResult]:
        """Load every generated CSV in FK-safe order. Return per-table results."""
        results: list[LoadResult] = []
        for table in self._order:
            csv_path = self._find_csv(table)
            if csv_path is None:
                logger.debug("No generated CSV found for %s — skipping.", table)
                continue
            result = self._load_table(table, csv_path)
            results.append(result)
        return results

    def load_one(self, table_name: str) -> LoadResult:
        """Load a single table's generated CSV."""
        csv_path = self._find_csv(table_name)
        if csv_path is None:
            return LoadResult(table_name=table_name, rows_loaded=0, success=False,
                              error=f"No CSV found for {table_name}")
        return self._load_table(table_name, csv_path)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _find_csv(self, table_name: str) -> Optional[Path]:
        """Locate generated CSV (case-insensitive)."""
        target = table_name.upper()
        for path in self._dir.glob("*.csv"):
            if path.stem.upper() == target:
                return path
        return None

    def _load_table(self, table_name: str, csv_path: Path) -> LoadResult:
        """TRUNCATE + bulk INSERT from csv_path into schema.table_name."""
        conn = self._db.connect()
        full_table = f"{self._schema}.{table_name}"

        try:
            # 1. Optional truncate
            if self._truncate:
                with conn.cursor() as cur:
                    cur.execute(f"TRUNCATE TABLE {full_table} CASCADE;")
                logger.debug("Truncated %s.", full_table)

            # 2. Read generated CSV
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader  = csv.DictReader(fh)
                headers = reader.fieldnames or []
                rows    = list(reader)

            if not rows:
                logger.warning("  %s — generated CSV is empty.", table_name)
                return LoadResult(table_name=table_name, rows_loaded=0, success=True)

            # 3. Build INSERT SQL
            pg_cols      = [h.lower() for h in headers]
            col_list     = ", ".join(pg_cols)
            placeholders = ", ".join(["%s"] * len(pg_cols))
            insert_sql   = (
                f"INSERT INTO {full_table} ({col_list})\n"
                f"VALUES ({placeholders})\n"
                f"ON CONFLICT DO NOTHING;"
            )

            # 4. Build value tuples (apply Oracle date normalisation)
            value_rows: list[tuple] = []
            for row in rows:
                values = []
                for h in headers:
                    raw = row.get(h, "") or ""
                    if raw == "" or raw.strip() == "":
                        values.append(None)
                    else:
                        values.append(_format_datetime_for_pg(raw))
                value_rows.append(tuple(values))

            # 5. executemany
            with conn.cursor() as cur:
                cur.executemany(insert_sql, value_rows)

            logger.info("  Loaded %d rows into %s.", len(value_rows), full_table)
            return LoadResult(
                table_name=table_name, rows_loaded=len(value_rows), success=True
            )

        except Exception as exc:  # noqa: BLE001
            logger.error("  Failed to load %s: %s", full_table, exc)
            return LoadResult(
                table_name=table_name, rows_loaded=0,
                success=False, error=str(exc),
            )
