"""
generate_data.py — CLI entry point for the Smart Test Data Generator.

Workflow
────────
1. Scan input/data/*.csv  →  PatternAnalyzer learns per-column profiles.
2. DataGenerator synthesizes ROWS_PER_TABLE rows per table.
3. Generated CSVs are written to generated_data/.
4. (Optional) PostgresLoader bulk-loads them into PostgreSQL.

Usage
─────
    python generate_data.py                      # generate + load (default)
    python generate_data.py --generate-only      # write CSVs, skip DB
    python generate_data.py --rows 500           # override row count
    python generate_data.py --schema staging     # different schema
    python generate_data.py --table p_dtl_tb     # single table only
    python generate_data.py --no-truncate        # insert without TRUNCATE
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import config as cfg
from config import (
    DB_CONFIG,
    GENERATED_DATA_DIR,
    INPUT_DATA_DIR,
    OUTPUT_LOGS_DIR,
    PG_SCHEMA,
    RANDOM_SEED,
    ROWS_PER_TABLE,
)
from data_generator import DataGenerator
from db import DatabaseManager
from pattern_analyzer import PatternAnalyzer
from postgres_loader import PostgresLoader


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    OUTPUT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = OUTPUT_LOGS_DIR / f"generate_{ts}.log"

    fmt = "%(asctime)s  [%(levelname)-8s]  %(message)s"
    logging.basicConfig(
        level    = logging.INFO,
        format   = fmt,
        handlers = [
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger(__name__).info("Log -> %s", log_file)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog        = "generate_data.py",
        description = "Smart Test Data Generator for Oracle -> PostgreSQL",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = """
Examples:
  python generate_data.py                    # full pipeline
  python generate_data.py --generate-only   # CSVs only, no DB
  python generate_data.py --rows 500        # 500 rows per table
  python generate_data.py --table p_dtl_tb  # single table
        """,
    )
    parser.add_argument("--generate-only", action="store_true",
                        help="Write CSVs but skip loading into PostgreSQL.")
    parser.add_argument("--no-truncate", action="store_true",
                        help="Skip TRUNCATE before loading (use ON CONFLICT DO NOTHING).")
    parser.add_argument("--rows", type=int, default=ROWS_PER_TABLE,
                        metavar="N", help=f"Rows per table (default: {ROWS_PER_TABLE}).")
    parser.add_argument("--schema", default=PG_SCHEMA,
                        metavar="SCHEMA", help=f"Target PG schema (default: {PG_SCHEMA!r}).")
    parser.add_argument("--table", default=None,
                        metavar="TABLE", help="Generate/load a single table only.")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED,
                        metavar="SEED", help=f"Random seed (default: {RANDOM_SEED}).")
    return parser


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_summary(
    gen_results : dict[str, int],
    load_results: list | None,
    rows_per_tbl: int,
    fk_report   : dict[tuple[str, str], int] | None = None,
) -> None:
    total_tables = len(gen_results)
    total_gen    = sum(gen_results.values())
    gen_ok       = sum(1 for v in gen_results.values() if v == rows_per_tbl)
    gen_failed   = total_tables - gen_ok

    divider = "-" * 56
    print(f"\n{divider}")
    print("  SMART TEST DATA GENERATOR  :  SUMMARY")
    print(divider)
    print(f"  Tables processed    :  {total_tables}")
    print(f"  Rows generated      :  {total_gen:,}")
    print(f"  Generation OK       :  {gen_ok}")
    print(f"  Generation failed   :  {gen_failed}")

    if load_results is not None:
        total_loaded = sum(r.rows_loaded for r in load_results)
        load_ok      = sum(1 for r in load_results if r.success)
        load_fail    = len(load_results) - load_ok
        print(f"  Rows loaded to PG   :  {total_loaded:,}")
        print(f"  Load OK             :  {load_ok}")
        print(f"  Load failed         :  {load_fail}")

    print(divider)

    # Report failures
    failures = [(t, v) for t, v in gen_results.items() if v < rows_per_tbl]
    if failures:
        print("\n  * Generation issues:")
        for t, v in failures:
            print(f"    {t:<40} ({v} rows)")

    if load_results:
        load_failures = [r for r in load_results if not r.success]
        if load_failures:
            print("\n  * Load failures:")
            for r in load_failures:
                print(f"    {r.table_name:<40} {r.error or ''}")

    if fk_report:
        print("\n  * FK Integrity Failures (Orphans):")
        for (ct, cc), orphans in fk_report.items():
            print(f"    {ct}.{cc:<25} -> {orphans} orphans")
    elif fk_report is not None:
        print("\n  * FK Integrity : PASSED (0 orphans across all generated rows)")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Force UTF-8 on Windows
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _setup_logging()
    log = logging.getLogger(__name__)

    parser = _build_parser()
    args   = parser.parse_args()

    rows        = args.rows
    schema      = args.schema
    seed        = args.seed
    single_tbl  = args.table.lower() if args.table else None
    gen_only    = args.generate_only
    no_truncate = args.no_truncate

    GENERATED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Pattern Analysis ─────────────────────────────────────── #
    log.info("=" * 56)
    log.info("Reading sample CSVs from %s ...", INPUT_DATA_DIR)
    analyzer = PatternAnalyzer(INPUT_DATA_DIR)
    all_profiles = analyzer.analyze_all()
    log.info("Learning patterns ... %d tables analyzed.", len(all_profiles))

    # Filter to single table if requested
    if single_tbl:
        if single_tbl not in all_profiles:
            log.error("Table '%s' not found in sample data. Available: %s",
                      single_tbl, sorted(all_profiles.keys()))
            sys.exit(1)
        all_profiles = {single_tbl: all_profiles[single_tbl]}

    # ── Step 2: Data Generation ──────────────────────────────────────── #
    log.info("Generating %d rows per table ...", rows)
    generator = DataGenerator(
        profiles       = all_profiles,
        output_dir     = GENERATED_DATA_DIR,
        rows_per_table = rows,
        seed           = seed,
    )
    gen_results = generator.generate_all()
    total_gen   = sum(gen_results.values())
    log.info("Saving CSVs to %s ...", GENERATED_DATA_DIR)
    log.info("Generated %d rows across %d tables.", total_gen, len(gen_results))

    # ── Step 2.5: FK Integrity Audit ─────────────────────────────────── #
    fk_report = generator.validate_fk_integrity()
    if fk_report:
        log.warning("FK INTEGRITY FAILURES DETECTED!")
    else:
        log.info("FK Integrity Audit: PASSED (Zero orphans).")

    # ── Step 3: PostgreSQL Load ──────────────────────────────────────── #
    load_results = None
    if not gen_only:
        log.info("Loading into PostgreSQL (schema: %s) ...", schema)
        db = DatabaseManager(DB_CONFIG)
        try:
            loader = PostgresLoader(
                db_manager  = db,
                schema      = schema,
                data_dir    = GENERATED_DATA_DIR,
                table_order = generator._order,
                truncate    = not no_truncate,
            )
            load_results = loader.load_all()
            total_loaded = sum(r.rows_loaded for r in load_results)
            log.info("Loaded %d rows into PostgreSQL.", total_loaded)
        finally:
            db.disconnect()
    else:
        log.info("--generate-only: skipping PostgreSQL load.")

    # ── Step 4: Summary ──────────────────────────────────────────────── #
    log.info("Completed.")
    _print_summary(gen_results, load_results, rows, fk_report)


if __name__ == "__main__":
    main()
