"""
converter.py — Orchestrator and CLI entry point for the Oracle → PostgreSQL
DDL converter.

Workflow
────────
1.  Discover every *.sql file in ``input/ddl/``.
2.  Convert each file using :class:`DDLConverter`.
3.  Write the resulting PostgreSQL SQL to ``output/ddl/<stem>.sql``.
4.  (Optional) Connect to PostgreSQL and execute the DDL.
5.  Collect all deferred FK ALTER TABLE statements.
6.  Write ``output/ddl/all_fk_constraints.sql``.
7.  Execute all FK statements after every table has been created.
8.  (Optional) Load sample CSV data for each table.
9.  Print a formatted execution summary.

Usage
─────
    python converter.py                    # full pipeline (convert + execute)
    python converter.py --convert-only     # DDL conversion only, no DB needed
    python converter.py --no-data          # skip sample-data loading
    python converter.py --schema staging   # override target schema at runtime
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import config as cfg
from config import (
    DB_CONFIG,
    INPUT_DATA_DIR,
    INPUT_DDL_DIR,
    OUTPUT_DDL_DIR,
    OUTPUT_LOGS_DIR,
    PG_SCHEMA,
)
from data_loader import DataLoader
from db import DatabaseManager
from ddl_converter import DDLConverter, TableConversion
from sql_executor import SQLExecutor


# ─────────────────────────────────────────────────────────────────────────── #
# Per-table summary record                                                     #
# ─────────────────────────────────────────────────────────────────────────── #


@dataclass
class TableSummary:
    """Aggregated outcome for one Oracle DDL file."""
    table_name        : str
    source_file       : str
    conversion_ok     : bool
    execution_ok      : bool   = False
    data_rows_loaded  : int    = 0
    error             : Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────── #
# Main orchestrator                                                            #
# ─────────────────────────────────────────────────────────────────────────── #


class OracleToPostgresConverter:
    """
    Drives the end-to-end Oracle → PostgreSQL conversion pipeline.

    Parameters
    ----------
    schema   : str
        Target PostgreSQL schema (overrides the value in ``.env``).
    execute  : bool
        When *True* the converted DDL is also executed in PostgreSQL.
    load_data: bool
        When *True* sample CSV rows are inserted after each table.
    """

    def __init__(
        self,
        schema   : str  = PG_SCHEMA,
        execute  : bool = True,
        load_data: bool = True,
    ) -> None:
        self._schema     = schema
        self._execute    = execute
        self._load_data  = load_data

        self._ddl_conv   : DDLConverter       = DDLConverter(schema=schema)
        self._db_mgr     : Optional[DatabaseManager]   = None
        self._executor   : Optional[SQLExecutor]       = None
        self._loader     : Optional[DataLoader]        = None

        self._summaries  : list[TableSummary] = []
        self._all_fks    : list[str]           = []

        self._setup_logging()
        self._ensure_directories()

    # ------------------------------------------------------------------ #
    # Setup helpers                                                        #
    # ------------------------------------------------------------------ #

    def _setup_logging(self) -> None:
        OUTPUT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = OUTPUT_LOGS_DIR / f"conversion_{ts}.log"

        fmt = "%(asctime)s  [%(levelname)-8s]  %(message)s"
        logging.basicConfig(
            level   =logging.INFO,
            format  =fmt,
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        self._log = logging.getLogger(__name__)
        self._log.info("Log → %s", log_file)

    def _ensure_directories(self) -> None:
        for d in [INPUT_DDL_DIR, INPUT_DATA_DIR, OUTPUT_DDL_DIR, OUTPUT_LOGS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    def _connect_db(self) -> bool:
        """Attempt to connect to PostgreSQL.  Returns True on success."""
        try:
            self._db_mgr  = DatabaseManager(DB_CONFIG)
            self._executor = SQLExecutor(self._db_mgr, self._schema)
            self._loader   = DataLoader(self._db_mgr, self._schema, INPUT_DATA_DIR)
            self._executor.ensure_schema()
            return True
        except Exception as exc:  # noqa: BLE001
            self._log.error("Database connection failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Public run method                                                    #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Execute the complete conversion pipeline."""
        ddl_files = sorted(INPUT_DDL_DIR.glob("*.sql"))

        if not ddl_files:
            self._log.warning(
                "No *.sql files found in %s  —  nothing to do.", INPUT_DDL_DIR
            )
            return

        self._log.info(
            "Found %d DDL file(s) in %s", len(ddl_files), INPUT_DDL_DIR
        )

        # Connect to database (unless convert-only mode)
        if self._execute:
            ok = self._connect_db()
            if not ok:
                self._log.warning(
                    "Proceeding in convert-only mode (no DB connection)."
                )
                self._execute = False

        # ── Phase 1: Convert + execute each table ────────────────────── #
        for sql_file in ddl_files:
            self._process_file(sql_file)

        # -- Phase 2: Write + execute deferred FK constraints ----------- #
        self._write_fk_file(self._all_fks)

        if self._execute and self._all_fks and self._executor:
            self._log.info(
                "Applying %d deferred FK constraint(s) ...", len(self._all_fks)
            )
            fk_results = self._executor.execute_fk_statements(self._all_fks)
            fk_ok   = sum(1 for r in fk_results if r.success)
            fk_fail = len(fk_results) - fk_ok
            self._log.info(
                "FK constraints: %d succeeded, %d failed.", fk_ok, fk_fail
            )

        # -- Phase 3: Summary ------------------------------------------- #
        self._print_summary()

        # Tidy up
        if self._db_mgr:
            self._db_mgr.disconnect()

    # ------------------------------------------------------------------ #
    # Per-file processing                                                  #
    # ------------------------------------------------------------------ #

    def _process_file(self, sql_file: Path) -> None:
        sep = "-" * 60
        self._log.info(sep)
        self._log.info("Reading   %s", sql_file.name)

        # -- Conversion ------------------------------------------------- #
        self._log.info("Converting %s ...", sql_file.name)
        result: TableConversion = self._ddl_conv.convert_file(sql_file)

        # -- Write output SQL ------------------------------------------- #
        if result.pg_statements:
            self._log.info("Writing PostgreSQL SQL ...")
            self._write_output(sql_file.stem, result)

        summary = TableSummary(
            table_name   =result.table_name,
            source_file  =sql_file.name,
            conversion_ok=result.success,
            error        =result.error,
        )

        if not result.success:
            self._log.error("Conversion FAILED: %s", result.error)
            self._summaries.append(summary)
            return

        # Collect FK statements for the deferred phase
        self._all_fks.extend(result.fk_statements)

        # -- Execution -------------------------------------------------- #
        if self._execute and self._executor:
            self._log.info("Executing  %s ...", result.table_name)
            exec_res = self._executor.execute_table_ddl(
                result.table_name, result.pg_statements
            )
            summary.execution_ok = exec_res.success
            summary.error        = exec_res.error

            if exec_res.success:
                self._log.info(
                    "Executed %d statement(s) for %s.",
                    exec_res.statements_executed, result.table_name,
                )
                # -- Sample data --------------------------------------- #
                if self._load_data and self._loader:
                    self._log.info("Loading sample data ...")
                    load_res = self._loader.load_sample_data(result.table_name)
                    summary.data_rows_loaded = load_res.rows_loaded
                    if load_res.rows_loaded:
                        self._log.info(
                            "Loaded %d sample row(s) into %s.%s.",
                            load_res.rows_loaded, self._schema, result.table_name,
                        )
            else:
                self._log.error(
                    "Execution FAILED for %s: %s",
                    result.table_name, exec_res.error,
                )
        else:
            # No DB — mark as N/A (counts as "successful" for conversion)
            summary.execution_ok = not self._execute

        self._log.info("Completed. %s", result.table_name)
        self._summaries.append(summary)

    # ------------------------------------------------------------------ #
    # Output file writers                                                  #
    # ------------------------------------------------------------------ #

    def _write_output(self, stem: str, result: TableConversion) -> None:
        """Write the converted PostgreSQL DDL to ``output/ddl/<stem>.sql``."""
        out_path = OUTPUT_DDL_DIR / f"{stem}.sql"

        header = "\n".join([
            "-- " + "=" * 66,
            f"-- PostgreSQL DDL  :  {self._schema}.{result.table_name}",
            f"-- Source file     :  {result.source_file}",
            f"-- Generated       :  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"-- Schema          :  {self._schema}",
            "-- " + "=" * 66,
            "",
            f"SET search_path TO {self._schema};",
            "",
        ])

        body = "\n\n".join(s for s in result.pg_statements if s.strip())

        fk_note = ""
        if result.fk_statements:
            fk_note = (
                "\n\n-- "
                + "-" * 66
                + "\n-- Foreign key constraints for this table are stored in:\n"
                + "--   output/ddl/all_fk_constraints.sql\n"
                + "-- " + "-" * 66
            )

        out_path.write_text(header + body + fk_note + "\n", encoding="utf-8")
        self._log.debug("Wrote %s", out_path)

    def _write_fk_file(self, fk_statements: list[str]) -> None:
        """Write all deferred FK statements to ``output/ddl/all_fk_constraints.sql``."""
        fk_path = OUTPUT_DDL_DIR / "all_fk_constraints.sql"

        header = "\n".join([
            "-- " + "=" * 66,
            "-- All Foreign Key Constraints",
            "-- Run this script AFTER all tables have been created.",
            f"-- Generated  :  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"-- Schema     :  {self._schema}",
            "-- " + "=" * 66,
            "",
            f"SET search_path TO {self._schema};",
            "",
        ])

        if fk_statements:
            body = "\n\n".join(s for s in fk_statements if s.strip())
        else:
            body = "-- No foreign key constraints were found."

        fk_path.write_text(header + body + "\n", encoding="utf-8")
        self._log.info("FK file written -> %s", fk_path)

    # ------------------------------------------------------------------ #
    # Summary report                                                       #
    # ------------------------------------------------------------------ #

    def _print_summary(self) -> None:
        total     = len(self._summaries)
        conv_ok   = sum(1 for s in self._summaries if s.conversion_ok)
        exec_ok   = sum(1 for s in self._summaries if s.execution_ok)
        failed    = sum(
            1 for s in self._summaries
            if not s.conversion_ok or (self._execute and not s.execution_ok)
        )
        total_rows = sum(s.data_rows_loaded for s in self._summaries)

        divider = "-" * 52
        print(f"\n{divider}")
        print("  ORACLE -> POSTGRESQL  :  CONVERSION SUMMARY")
        print(divider)
        print(f"  Total Tables        :  {total}")
        print(f"  Conversion OK       :  {conv_ok}")
        if self._execute:
            print(f"  Execution OK        :  {exec_ok}")
        print(f"  Failed              :  {failed}")
        if self._load_data:
            print(f"  Sample Rows Loaded  :  {total_rows}")
        print(divider)

        if failed:
            print("\n  * Failed tables:")
            for s in self._summaries:
                is_fail = not s.conversion_ok or (
                    self._execute and not s.execution_ok
                )
                if is_fail:
                    tag = "CONV" if not s.conversion_ok else "EXEC"
                    print(f"    [{tag}]  {s.table_name:<32} ({s.source_file})")
                    if s.error:
                        print(f"           {s.error[:110]}")

        print()
        self._log.info("Summary complete.  Total=%d  OK=%d  Failed=%d",
                        total, conv_ok if not self._execute else exec_ok, failed)


# ─────────────────────────────────────────────────────────────────────────── #
# CLI entry point                                                              #
# ─────────────────────────────────────────────────────────────────────────── #


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog       ="converter.py",
        description="Oracle DDL → PostgreSQL DDL Converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python converter.py                        Full pipeline (convert + execute + data)
  python converter.py --convert-only         Convert only; no database connection
  python converter.py --no-data              Convert + execute; skip CSV data loading
  python converter.py --schema staging       Use a different target schema
        """,
    )
    parser.add_argument(
        "--convert-only",
        action ="store_true",
        default=False,
        help   ="Convert DDL files only; do not connect to PostgreSQL.",
    )
    parser.add_argument(
        "--no-data",
        action ="store_true",
        default=False,
        help   ="Skip loading sample data from CSV files.",
    )
    parser.add_argument(
        "--schema",
        default=PG_SCHEMA,
        metavar="SCHEMA",
        help   =f"Target PostgreSQL schema (default: {PG_SCHEMA!r}).",
    )
    return parser


def main() -> None:
    # Force UTF-8 output on Windows to prevent console encoding crashes
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = _build_arg_parser()
    args   = parser.parse_args()

    converter = OracleToPostgresConverter(
        schema   =args.schema,
        execute  =not args.convert_only,
        load_data=not args.no_data,
    )
    converter.run()


if __name__ == "__main__":
    main()
