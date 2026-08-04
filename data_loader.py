"""
data_loader.py — Load sample data from CSV files into PostgreSQL.

For every successfully created table the converter looks for a corresponding
``.csv`` file in ``input/data/`` whose stem matches the table name
(case-insensitive).

CSV format
──────────
• First row  : column names (must match the PostgreSQL column names exactly,
               but comparison is case-insensitive).
• Data rows  : at most 2 sample rows (only first 2 are inserted).
• Empty cells: treated as SQL NULL.
• Duplicate PK values from previous runs: silently ignored via
  ``ON CONFLICT DO NOTHING``.

The loader resolves column names to their PostgreSQL (lowercase) equivalents
at runtime so CSV headers can be in any case.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from db import DatabaseManager

logger = logging.getLogger(__name__)

# Maximum sample rows to insert per table
_MAX_ROWS = 2

# Month mapping for Oracle → PostgreSQL datetime parsing
_MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"
}


def _format_datetime_for_pg(val: str) -> str:
    """Format Oracle date/timestamp string to a standard ISO PostgreSQL-compatible string."""
    val = val.strip()
    if not val:
        return val

    # Match DD-MON-YY or DD-MON-YYYY followed by optional time
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})(?:\s+(.*))?$", val)
    if not m:
        return val

    day = m.group(1).zfill(2)
    mon_str = m.group(2).upper()
    year = m.group(3)
    time_part = m.group(4)

    month = _MONTHS.get(mon_str)
    if not month:
        return val

    if len(year) == 2:
        y_int = int(year)
        if y_int >= 50:
            year = f"19{year}"
        else:
            year = f"20{year}"

    if not time_part:
        return f"{year}-{month}-{day}"

    # Match time format: HH.MI.SS.FFFFFF AM/PM or HH:MI:SS.FFFFFF AM/PM
    m_time = re.match(
        r"^(\d{1,2})[.:](\d{1,2})[.:](\d{1,2})(?:\.(\d+))?(?:\s+(AM|PM))?$",
        time_part.strip(),
        re.IGNORECASE,
    )
    if not m_time:
        return f"{year}-{month}-{day} {time_part}"

    hour = int(m_time.group(1))
    minute = m_time.group(2).zfill(2)
    second = m_time.group(3).zfill(2)
    fraction = m_time.group(4) or ""
    meridian = m_time.group(5)

    if meridian:
        meridian = meridian.upper()
        if meridian == "PM" and hour < 12:
            hour += 12
        elif meridian == "AM" and hour == 12:
            hour = 0

    hour_str = str(hour).zfill(2)

    if fraction:
        fraction = fraction[:6].ljust(6, "0")
        return f"{year}-{month}-{day} {hour_str}:{minute}:{second}.{fraction}"

    return f"{year}-{month}-{day} {hour_str}:{minute}:{second}"


@dataclass
class LoadResult:
    """Outcome of loading sample data for one table."""
    table_name : str
    rows_loaded: int
    success    : bool
    error      : Optional[str] = None


class DataLoader:
    """
    Inserts up to :data:`_MAX_ROWS` sample rows from a CSV file into the
    corresponding PostgreSQL table.

    Parameters
    ----------
    db_manager : DatabaseManager
        Open database connection manager.
    schema     : str
        Target PostgreSQL schema name.
    data_dir   : Path
        Directory that holds the ``<table_name>.csv`` sample-data files.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        schema    : str,
        data_dir  : Path,
    ) -> None:
        self._db       = db_manager
        self._schema   = schema
        self._data_dir = data_dir

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load_sample_data(self, table_name: str) -> LoadResult:
        """
        Find and load the CSV file for *table_name*.

        If no matching CSV file exists the call succeeds with 0 rows loaded.
        """
        csv_path = self._find_csv(table_name)
        if csv_path is None:
            logger.debug("No CSV found for table '%s' — skipping.", table_name)
            return LoadResult(table_name=table_name, rows_loaded=0, success=True)

        logger.info("  Loading sample data from %s …", csv_path.name)
        try:
            return self._load_csv(table_name, csv_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("CSV load failed for '%s': %s", table_name, exc)
            return LoadResult(
                table_name=table_name, rows_loaded=0,
                success=False, error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _find_csv(self, table_name: str) -> Optional[Path]:
        """
        Return the CSV path whose stem matches *table_name* or
        *table_name_DATA_TABLE* (case-insensitive), or *None* if no file is found.
        """
        # Exact match first
        exact = self._data_dir / f"{table_name}.csv"
        if exact.exists():
            return exact

        exact_dt = self._data_dir / f"{table_name}_DATA_TABLE.csv"
        if exact_dt.exists():
            return exact_dt

        # Case-insensitive scan
        target = table_name.upper()
        target_dt = f"{target}_DATA_TABLE"
        for path in self._data_dir.glob("*.csv"):
            stem_upper = path.stem.upper()
            if stem_upper == target or stem_upper == target_dt:
                return path

        return None

    def _load_csv(self, table_name: str, csv_path: Path) -> LoadResult:
        """
        Read the CSV and insert up to :data:`_MAX_ROWS` rows.

        Uses ``ON CONFLICT DO NOTHING`` so re-runs are idempotent.
        """
        rows_loaded = 0

        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            reader  = csv.DictReader(fh)
            headers = reader.fieldnames

            if not headers:
                logger.warning("CSV file %s has no headers.", csv_path.name)
                return LoadResult(table_name=table_name, rows_loaded=0, success=True)

            # Normalise CSV headers → lowercase for PostgreSQL
            pg_columns = [h.strip().lower() for h in headers]
            col_list   = ", ".join(pg_columns)
            placeholders = ", ".join(["%s"] * len(pg_columns))
            insert_sql = (
                f"INSERT INTO {self._schema}.{table_name} ({col_list})\n"
                f"VALUES ({placeholders})\n"
                f"ON CONFLICT DO NOTHING;"
            )

            conn = self._db.connect()
            with conn.cursor() as cur:
                for row_num, row in enumerate(reader, start=1):
                    if row_num > _MAX_ROWS:
                        break

                    values: list[Optional[str]] = []
                    for header in headers:
                        raw = row.get(header, "")
                        if raw is None or raw.strip() == "":
                            values.append(None)
                        else:
                            # Apply Oracle timestamp/date parsing and format conversion
                            formatted_val = _format_datetime_for_pg(raw)
                            values.append(formatted_val)

                    try:
                        cur.execute(insert_sql, values)
                        rows_loaded += 1
                        logger.debug(
                            "  Inserted row %d into %s.%s",
                            row_num, self._schema, table_name,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "  Row %d insert failed for '%s': %s",
                            row_num, table_name, exc,
                        )

        return LoadResult(table_name=table_name, rows_loaded=rows_loaded, success=True)
