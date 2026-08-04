"""
config.py — Central configuration for Oracle → PostgreSQL DDL Converter.

All settings are read from a .env file in the project root.
Sensible defaults are provided so the converter runs without any .env file
(useful for convert-only mode when no DB is needed).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the project root (the directory that contains this file)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
BASE_DIR        : Path = _PROJECT_ROOT
INPUT_DDL_DIR   : Path = BASE_DIR / "input" / "ddl"
INPUT_DATA_DIR  : Path = BASE_DIR / "input" / "data"
OUTPUT_DDL_DIR  : Path = BASE_DIR / "output" / "ddl"
OUTPUT_LOGS_DIR : Path = BASE_DIR / "output" / "logs"

# ---------------------------------------------------------------------------
# PostgreSQL target schema
# ---------------------------------------------------------------------------
PG_SCHEMA: str = os.getenv("PG_SCHEMA", "provider")

# ---------------------------------------------------------------------------
# PostgreSQL connection parameters
# ---------------------------------------------------------------------------
DB_CONFIG: dict[str, str | int] = {
    "host"    : os.getenv("DB_HOST",     "localhost"),
    "port"    : int(os.getenv("DB_PORT", "5432")),
    "dbname"  : os.getenv("DB_NAME",     "postgres"),
    "user"    : os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# ---------------------------------------------------------------------------
# Runtime behaviour flags  (can also be overridden via .env)
# ---------------------------------------------------------------------------
EXECUTE_DDL     : bool = os.getenv("EXECUTE_DDL",      "true").lower() == "true"
LOAD_SAMPLE_DATA: bool = os.getenv("LOAD_SAMPLE_DATA", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Smart Test Data Generator
# ---------------------------------------------------------------------------
GENERATED_DATA_DIR : Path = BASE_DIR / "generated_data"
ROWS_PER_TABLE     : int  = int(os.getenv("ROWS_PER_TABLE", "1000"))
RANDOM_SEED        : int  = int(os.getenv("RANDOM_SEED",    "12345"))
AUDIT_USER         : str  = os.getenv("AUDIT_USER",         "SYSTEM")
