"""
datatype_mapper.py — Oracle → PostgreSQL data-type mapping engine.

All conversions are implemented as a single public method `map_type(oracle_type)`
that accepts the raw Oracle type string (with or without precision/scale) and
returns the appropriate PostgreSQL type string.

Mapping rules
─────────────
NUMBER(1,0)  / NUMBER(2,0)             → SMALLINT
NUMBER(3,0)  .. NUMBER(8,0)            → INTEGER
NUMBER(9,0)  .. NUMBER(38,0)           → BIGINT
NUMBER(p,s)  where s > 0              → NUMERIC(p,s)
NUMBER(p)    (no scale)               → integer type by precision, else NUMERIC(p)
NUMBER        (bare)                  → NUMERIC
VARCHAR2(n [BYTE|CHAR])               → VARCHAR(n)
NVARCHAR2(n)                          → VARCHAR(n)
CHAR(n [BYTE|CHAR])                   → CHAR(n)
NCHAR(n)                              → CHAR(n)
DATE                                  → DATE
TIMESTAMP(n)                          → TIMESTAMP
CLOB / NCLOB                          → TEXT
BLOB                                  → BYTEA
RAW(n) / RAW                          → BYTEA
LONG RAW                              → BYTEA
LONG                                  → TEXT
XMLTYPE                               → XML
FLOAT(n) / FLOAT                      → DOUBLE PRECISION
INTEGER / SMALLINT / BIGINT           → preserved as-is
"""
from __future__ import annotations

import re


class DataTypeMapper:
    """Translates Oracle data types to PostgreSQL equivalents."""

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _integer_type_for_precision(precision: int) -> str:
        """
        Return the smallest integer type that can safely hold
        a NUMBER with `precision` digits and scale = 0.
        """
        if precision <= 2:      # max 99
            return "SMALLINT"
        if precision <= 8:      # max 99,999,999  (fits in INTEGER: 2^31-1)
            return "INTEGER"
        return "BIGINT"         # precision 9-38

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def map_type(self, oracle_type: str) -> str:
        """
        Convert an Oracle type string to its PostgreSQL equivalent.

        Parameters
        ----------
        oracle_type : str
            Raw Oracle type string as it appears in the DDL, e.g.
            ``NUMBER(10,0)``, ``VARCHAR2(200 BYTE)``, ``TIMESTAMP(6)``.

        Returns
        -------
        str
            PostgreSQL type string, e.g. ``BIGINT``, ``VARCHAR(200)``.
        """
        t = oracle_type.strip()
        u = t.upper()

        # ── NUMBER family ────────────────────────────────────────────────

        # NUMBER(p, 0)  → integer type
        m = re.match(r"NUMBER\s*\(\s*(\d+)\s*,\s*0\s*\)$", u)
        if m:
            return self._integer_type_for_precision(int(m.group(1)))

        # NUMBER(p, s) where s > 0  → NUMERIC(p,s)
        m = re.match(r"NUMBER\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", u)
        if m:
            p, s = m.group(1), m.group(2)
            if int(s) > 0:
                return f"NUMERIC({p},{s})"
            # s == 0 already handled above; but guard for odd formatting
            return self._integer_type_for_precision(int(p))

        # NUMBER(p)  → integer type if precision ≤ 18, else NUMERIC(p)
        m = re.match(r"NUMBER\s*\(\s*(\d+)\s*\)$", u)
        if m:
            p = int(m.group(1))
            if p <= 18:
                return self._integer_type_for_precision(p)
            return f"NUMERIC({p})"

        # Bare NUMBER  → NUMERIC
        if re.match(r"^NUMBER$", u):
            return "NUMERIC"

        # ── VARCHAR2 / NVARCHAR2 ─────────────────────────────────────────

        m = re.match(r"N?VARCHAR2\s*\(\s*(\d+)\s*(?:BYTE|CHAR)?\s*\)$", u)
        if m:
            return f"VARCHAR({m.group(1)})"

        # ── CHAR / NCHAR ─────────────────────────────────────────────────

        m = re.match(r"N?CHAR\s*\(\s*(\d+)\s*(?:BYTE|CHAR)?\s*\)$", u)
        if m:
            return f"CHAR({m.group(1)})"

        if re.match(r"^N?CHAR$", u):
            return "CHAR(1)"

        # ── TIMESTAMP ────────────────────────────────────────────────────

        if re.match(r"TIMESTAMP\s*\(\s*\d+\s*\)$", u):
            return "TIMESTAMP"

        if re.match(r"^TIMESTAMP$", u):
            return "TIMESTAMP"

        # ── DATE ─────────────────────────────────────────────────────────

        if u == "DATE":
            return "DATE"

        # ── LOB types ────────────────────────────────────────────────────

        if u in ("CLOB", "NCLOB"):
            return "TEXT"

        if u == "BLOB":
            return "BYTEA"

        # ── RAW / LONG RAW ───────────────────────────────────────────────

        if re.match(r"^RAW\s*\(\s*\d+\s*\)$", u) or u == "RAW":
            return "BYTEA"

        if u == "LONG RAW":
            return "BYTEA"

        # ── LONG ─────────────────────────────────────────────────────────

        if u == "LONG":
            return "TEXT"

        # ── FLOAT ────────────────────────────────────────────────────────

        if re.match(r"^FLOAT(\s*\(\s*\d+\s*\))?$", u):
            return "DOUBLE PRECISION"

        # ── XMLTYPE ──────────────────────────────────────────────────────

        if u == "XMLTYPE":
            return "XML"

        # ── Pass-through (already PG-compatible) ─────────────────────────

        passthrough = {
            "INTEGER", "SMALLINT", "BIGINT",
            "NUMERIC", "DECIMAL",
            "BOOLEAN",
            "DOUBLE PRECISION",
            "REAL",
            "TEXT",
            "BYTEA",
            "DATE",
            "TIMESTAMP",
            "TIMESTAMPTZ",
        }
        if u in passthrough:
            return u

        # ── Unknown — return as-is so the converter keeps moving ─────────
        return t
