"""
ddl_converter.py — Core Oracle DDL → PostgreSQL DDL converter.

Converts one Oracle .sql file at a time.  Each file typically contains:
  - One CREATE TABLE statement
  - One or more CREATE [UNIQUE] INDEX statements
  - COMMENT ON TABLE / COMMENT ON COLUMN statements
  - Optionally ALTER TABLE ADD CONSTRAINT statements

Conversion rules
────────────────
• Oracle physical-storage clauses are silently dropped
  (TABLESPACE, SEGMENT CREATION, PCTFREE, INITRANS, MAXTRANS,
   STORAGE(…), LOGGING, COMPRESS, BUFFER_POOL, etc.)
• Data types are mapped via DataTypeMapper
• DEFAULT SYSDATE → DEFAULT CURRENT_TIMESTAMP, etc.
• FOREIGN KEY constraints are extracted and returned separately
  so they can be applied after all tables exist.
• All identifiers are lowercased (Oracle UPPER → PG lower convention).
• Output is formatted with column-name / type-width alignment.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import sqlparse

from datatype_mapper import DataTypeMapper

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════ #
# Data classes                                                                #
# ═══════════════════════════════════════════════════════════════════════════ #


@dataclass
class ColumnInfo:
    """Structured representation of a single parsed column."""
    name    : str           # lowercase PostgreSQL identifier
    pg_type : str           # converted PostgreSQL type
    default : str = ""      # converted default expression (empty = none)
    not_null: bool = False


@dataclass
class TableConversion:
    """Outcome of converting one Oracle DDL source file."""
    source_file  : str
    table_name   : str                        # lowercase PG table name
    pg_statements: list[str] = field(default_factory=list)
    fk_statements: list[str] = field(default_factory=list)
    success      : bool = True
    error        : Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════ #
# DDLConverter                                                                #
# ═══════════════════════════════════════════════════════════════════════════ #


class DDLConverter:
    """
    Converts one Oracle DDL file into a list of PostgreSQL-compatible
    SQL statements plus a separate list of deferred FK ALTER statements.

    Parameters
    ----------
    schema : str
        Target PostgreSQL schema name (default: ``"provider"``).
    """

    def __init__(self, schema: str = "provider") -> None:
        self.schema  = schema
        self._mapper = DataTypeMapper()

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    def convert_file(self, filepath: Path) -> TableConversion:
        """Read *filepath* and return a :class:`TableConversion`."""
        source = filepath.name
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return TableConversion(
                source_file=source, table_name="unknown",
                success=False, error=str(exc),
            )

        table_name = self._first_table_name(content)
        result = TableConversion(source_file=source, table_name=table_name)
        try:
            pg_stmts, fk_stmts = self._convert_content(content)
            result.pg_statements = pg_stmts
            result.fk_statements = fk_stmts
        except Exception as exc:  # noqa: BLE001
            result.success = False
            result.error   = str(exc)
            logger.exception("Unhandled error while converting %s", source)

        return result

    # ------------------------------------------------------------------ #
    # Statement-level dispatcher                                           #
    # ------------------------------------------------------------------ #

    def _split_statements(self, content: str) -> list[str]:
        """
        Split DDL content into individual statements.
        Handles semicolons as delimiters, and also detects statements that lack
        semicolons by looking for keyword starts on new lines (outside of string literals).
        """
        statements = []
        current_stmt = []
        in_string = False
        in_line_comment = False
        in_block_comment = False
        
        i = 0
        n = len(content)
        
        keywords = ["CREATE TABLE", "COMMENT ON", "CREATE INDEX", "ALTER TABLE", "CREATE UNIQUE INDEX"]
        
        while i < n:
            ch = content[i]
            
            # Handle comments first
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                    current_stmt.append('\n')
                i += 1
                continue
                
            if in_block_comment:
                if ch == '*' and i + 1 < n and content[i+1] == '/':
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue
                
            # Handle string literals
            if in_string:
                current_stmt.append(ch)
                if ch == "'":
                    if i + 1 < n and content[i+1] == "'":
                        current_stmt.append("'")
                        i += 2
                        continue
                    else:
                        in_string = False
                i += 1
                continue
                
            # Check comment starts
            if ch == '-' and i + 1 < n and content[i+1] == '-':
                in_line_comment = True
                i += 2
                continue
            if ch == '/' and i + 1 < n and content[i+1] == '*':
                in_block_comment = True
                i += 2
                continue
                
            # Check string literal start
            if ch == "'":
                in_string = True
                current_stmt.append(ch)
                i += 1
                continue
                
            # Check semicolon delimiter
            if ch == ';':
                stmt_str = "".join(current_stmt).strip()
                if stmt_str:
                    statements.append(stmt_str)
                current_stmt = []
                i += 1
                continue
                
            # Detect new statement start without semicolon
            if current_stmt and (ch == '\n' or ch == '\r'):
                # Look ahead past any whitespace and line/block comments
                j = i
                is_keyword_start = False
                temp_in_line_comment = False
                temp_in_block_comment = False
                
                while j < n:
                    c = content[j]
                    if temp_in_line_comment:
                        if c == '\n':
                            temp_in_line_comment = False
                        j += 1
                        continue
                    if temp_in_block_comment:
                        if c == '*' and j + 1 < n and content[j+1] == '/':
                            temp_in_block_comment = False
                            j += 2
                        else:
                            j += 1
                        continue
                    if c.isspace():
                        j += 1
                        continue
                    if c == '-' and j + 1 < n and content[j+1] == '-':
                        temp_in_line_comment = True
                        j += 2
                        continue
                    if c == '/' and j + 1 < n and content[j+1] == '*':
                        temp_in_block_comment = True
                        j += 2
                        continue
                        
                    # Found a non-whitespace, non-comment character
                    remaining = content[j:j+50].upper()
                    for kw in keywords:
                        if remaining.startswith(kw):
                            kw_len = len(kw)
                            if kw_len < len(remaining) and not remaining[kw_len].isalnum():
                                is_keyword_start = True
                                break
                    break
                    
                if is_keyword_start:
                    stmt_str = "".join(current_stmt).strip()
                    if stmt_str:
                        statements.append(stmt_str)
                    current_stmt = []
                    i += 1
                    continue
            
            current_stmt.append(ch)
            i += 1
            
        if current_stmt:
            stmt_str = "".join(current_stmt).strip()
            if stmt_str:
                statements.append(stmt_str)
                
        return statements

    def _convert_content(self, content: str) -> tuple[list[str], list[str]]:
        pg_stmts : list[str] = []
        fk_stmts : list[str] = []

        for raw in self._split_statements(content):
            stmt = raw.strip().rstrip(";").strip()
            if not stmt:
                continue

            # Strip leading/trailing SQL comments from the statement
            stmt_clean = sqlparse.format(stmt, strip_comments=True).strip()
            if not stmt_clean:
                continue

            upper = re.sub(r"\s+", " ", stmt_clean).upper().lstrip()

            if upper.startswith("CREATE TABLE"):
                tbl_stmts, fks = self._convert_create_table(stmt_clean)
                pg_stmts.extend(tbl_stmts)
                fk_stmts.extend(fks)

            elif re.match(r"CREATE\s+(UNIQUE\s+)?(BITMAP\s+)?INDEX\b", upper):
                idx = self._convert_create_index(stmt_clean)
                if idx:
                    pg_stmts.append(idx)

            elif upper.startswith("COMMENT ON"):
                cmt = self._convert_comment(stmt_clean)
                if cmt:
                    pg_stmts.append(cmt)

            elif upper.startswith("ALTER TABLE"):
                alt_stmts, fks = self._convert_alter_table(stmt_clean)
                pg_stmts.extend(alt_stmts)
                fk_stmts.extend(fks)

            # All other Oracle statements (GRANT, AUDIT, etc.) are silently ignored.

        return pg_stmts, fk_stmts

    # ================================================================== #
    # CREATE TABLE                                                         #
    # ================================================================== #

    def _convert_create_table(self, stmt: str) -> tuple[list[str], list[str]]:
        tbl_name = self._object_name(stmt, after="TABLE")
        body     = self._paren_body(stmt)
        if body is None:
            raise ValueError(f"No parenthesised body for table: {tbl_name!r}")

        columns  : list[ColumnInfo] = []
        pk_defs  : list[str]        = []
        uq_defs  : list[str]        = []
        ck_defs  : list[str]        = []
        fk_stmts : list[str]        = []

        for raw_def in self._top_level_split(body):
            defn  = raw_def.strip()
            if not defn:
                continue
            upper = re.sub(r"\s+", " ", defn).upper().lstrip()

            if upper.startswith("CONSTRAINT"):
                pg_con, fk_sql = self._inline_constraint(defn, tbl_name)
                if fk_sql:
                    fk_stmts.append(fk_sql)
                elif pg_con:
                    up = pg_con.upper()
                    if "PRIMARY KEY" in up:
                        pk_defs.append(pg_con)
                    elif "UNIQUE" in up:
                        uq_defs.append(pg_con)
                    elif "CHECK" in up:
                        ck_defs.append(pg_con)

            elif upper.startswith("PRIMARY KEY"):
                pk_defs.append(self._bare_pk(defn))

            else:
                col = self._parse_column(defn)
                if col:
                    columns.append(col)

        constraints = pk_defs + uq_defs + ck_defs
        body_lines  = self._format_table_body(columns, constraints)

        drop_stmt = (
            f"DROP TABLE IF EXISTS {self.schema}.{tbl_name} CASCADE;"
        )
        create_stmt = (
            f"CREATE TABLE {self.schema}.{tbl_name} (\n"
            + ",\n".join(body_lines)
            + "\n);"
        )
        return [drop_stmt, create_stmt], fk_stmts

    # ── Column formatting with alignment ──────────────────────────────── #

    def _format_table_body(
        self,
        columns    : list[ColumnInfo],
        constraints: list[str],
    ) -> list[str]:
        """
        Return indented, width-aligned column lines followed by constraint lines.
        """
        lines: list[str] = []

        if columns:
            name_w = max(len(c.name)    for c in columns)
            type_w = max(len(c.pg_type) for c in columns)
        else:
            name_w = type_w = 0

        for col in columns:
            parts: list[str] = [
                f"    {col.name:<{name_w}}  {col.pg_type:<{type_w}}"
            ]
            if col.default:
                parts.append(f"  DEFAULT {col.default}")
            if col.not_null:
                parts.append("  NOT NULL")
            lines.append("".join(parts).rstrip())

        for con in constraints:
            lines.append(f"    {con}")

        return lines

    # ================================================================== #
    # Column parsing                                                       #
    # ================================================================== #

    def _parse_column(self, defn: str) -> Optional[ColumnInfo]:
        """
        Parse a single Oracle column definition and return a :class:`ColumnInfo`.

        Handles quoted/unquoted names, all type variants, DEFAULT (including
        single-quoted strings with embedded apostrophes), NULL/NOT NULL, and
        strips Oracle column-level keywords (ENABLE, VISIBLE, INVISIBLE, etc.).
        """
        m = re.match(r'"?([A-Za-z0-9_$#]+)"?\s+(.*)', defn, re.DOTALL)
        if not m:
            logger.warning("Cannot parse column definition: %.80s", defn)
            return None

        col_name = m.group(1).lower()
        rest     = m.group(2).strip()

        # Extract Oracle data type
        oracle_type, rest = self._split_type(rest)
        pg_type           = self._mapper.map_type(oracle_type)

        # Extract DEFAULT (respects quoted strings)
        default_raw, rest = self._extract_default(rest)
        default_val       = self._convert_default(default_raw) if default_raw else ""

        # NOT NULL detection
        not_null = bool(re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE))

        return ColumnInfo(
            name    =col_name,
            pg_type =pg_type,
            default =default_val,
            not_null=not_null,
        )

    # ── Type extraction ────────────────────────────────────────────────── #

    def _split_type(self, rest: str) -> tuple[str, str]:
        """
        Peel the Oracle data-type token from the front of *rest*.

        Returns ``(oracle_type_str, remaining_text)``.
        """
        # Types that carry (precision [,scale]) or (length [BYTE|CHAR])
        m = re.match(
            r"((?:NUMBER|VARCHAR2?|NVARCHAR2|N?CHAR|TIMESTAMP|RAW|FLOAT|"
            r"DECIMAL|NUMERIC|INTERVAL\s+\w+(?:\s+TO\s+\w+)?)\s*"
            r"(?:\([^)]+\))?)",
            rest, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip(), rest[m.end():]

        # Single-keyword types
        m = re.match(
            r"(LONG\s+RAW|DATE|CLOB|NCLOB|BLOB|XMLTYPE|LONG|INTEGER|"
            r"SMALLINT|BIGINT|DOUBLE\s+PRECISION|BOOLEAN|REAL)",
            rest, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip(), rest[m.end():]

        # Fallback: take the first non-space token
        m = re.match(r"(\S+)", rest)
        if m:
            return m.group(1), rest[m.end():]

        return rest.strip(), ""

    # ── DEFAULT extraction ─────────────────────────────────────────────── #

    def _extract_default(self, rest: str) -> tuple[str, str]:
        """
        Locate ``DEFAULT <value>`` in *rest* and extract the value token,
        correctly handling single-quoted strings (including ``''`` escapes).

        Returns ``(default_value, remaining_rest)`` where *remaining_rest*
        has the DEFAULT clause removed.  If no DEFAULT is found, returns
        ``("", rest)`` unchanged.
        """
        m = re.search(r"\bDEFAULT\b", rest, re.IGNORECASE)
        if not m:
            return "", rest

        after        = rest[m.end():].lstrip()
        value_chars : list[str] = []
        i            = 0
        in_quote     = False

        while i < len(after):
            ch = after[i]

            if ch == "'" and not in_quote:
                in_quote = True
                value_chars.append(ch)
                i += 1

            elif ch == "'" and in_quote:
                # Escaped apostrophe ''
                if i + 1 < len(after) and after[i + 1] == "'":
                    value_chars.extend(["'", "'"])
                    i += 2
                else:
                    in_quote = False
                    value_chars.append(ch)
                    i += 1

            elif in_quote:
                value_chars.append(ch)
                i += 1

            else:
                # Check for keywords that terminate the DEFAULT value
                tail = after[i:]
                if re.match(
                    r"[\s,]+(NOT\s+NULL|NULL\b|ENABLE\b|DISABLE\b|"
                    r"VISIBLE\b|INVISIBLE\b|CONSTRAINT\b|CHECK\b|"
                    r"PRIMARY\b|UNIQUE\b|REFERENCES\b)",
                    tail, re.IGNORECASE,
                ):
                    break
                value_chars.append(ch)
                i += 1

        default_val  = "".join(value_chars).strip().rstrip(",").strip()
        # Remove the DEFAULT clause from rest so NULL detection works cleanly
        remaining    = rest[: m.start()] + after[i:]
        return default_val, remaining

    def _convert_default(self, val: str) -> str:
        """Map Oracle DEFAULT expressions to PostgreSQL equivalents."""
        clean = val.rstrip(";").strip()
        up    = clean.upper()

        _MAP = {
            "SYSDATE"       : "CURRENT_TIMESTAMP",
            "SYSDATE()"     : "CURRENT_TIMESTAMP",
            "SYSTIMESTAMP"  : "CURRENT_TIMESTAMP",
            "SYS_GUID()"    : "gen_random_uuid()",
            "SYS_GUID"      : "gen_random_uuid()",
            "USER"          : "CURRENT_USER",
            "NULL"          : "NULL",
        }
        if up in _MAP:
            return _MAP[up]
        if up.startswith("TO_DATE("):
            return "CURRENT_DATE"
        if up.startswith("TO_TIMESTAMP("):
            return "CURRENT_TIMESTAMP"
        if up.startswith("TO_CHAR("):
            # Keep the literal string; strip Oracle function wrapper
            inner = re.search(r"'([^']*)'", clean)
            return f"'{inner.group(1)}'" if inner else clean

        return clean

    # ================================================================== #
    # Constraint parsing                                                   #
    # ================================================================== #

    def _inline_constraint(
        self, defn: str, table_name: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Parse ``CONSTRAINT <name> <type> (…)`` inside a CREATE TABLE body.

        Returns ``(pg_clause, None)`` for PK/UK/CHECK  or
                ``(None, fk_alter_sql)`` for FOREIGN KEY  or
                ``(None, None)`` on parse failure.
        """
        m = re.match(
            r"CONSTRAINT\s+\"?([A-Za-z0-9_$#]+)\"?\s+(.*)",
            defn, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            return None, None

        con_name = m.group(1).lower()
        body     = self._strip_constraint_options(m.group(2).strip())
        upper    = re.sub(r"\s+", " ", body).upper()

        if upper.startswith("PRIMARY KEY"):
            cols = self._col_list(body)
            if cols is not None:
                return f"CONSTRAINT {con_name} PRIMARY KEY ({cols})", None

        elif upper.startswith("UNIQUE"):
            cols = self._col_list(body)
            if cols is not None:
                return f"CONSTRAINT {con_name} UNIQUE ({cols})", None

        elif upper.startswith("FOREIGN KEY"):
            fk_sql = self._build_fk_alter(con_name, body, table_name)
            return None, fk_sql if fk_sql else None

        elif upper.startswith("CHECK"):
            cm = re.search(r"CHECK\s*\((.+)\)\s*$", body, re.IGNORECASE | re.DOTALL)
            if cm:
                return f"CONSTRAINT {con_name} CHECK ({cm.group(1).strip()})", None

        return None, None

    def _strip_constraint_options(self, body: str) -> str:
        """
        Remove Oracle-specific constraint trailing clauses:
        ``USING INDEX …``, ``ENABLE``, ``DISABLE``, ``NOVALIDATE``,
        ``VALIDATE``, ``RELY``, ``NORELY``.
        """
        # Remove USING INDEX and everything after it
        body = re.sub(
            r"\s+USING\s+INDEX\b.*",
            "", body, flags=re.IGNORECASE | re.DOTALL,
        )
        # Remove trailing Oracle keywords
        for kw in ("ENABLE", "DISABLE", "NOVALIDATE", "VALIDATE", "RELY", "NORELY"):
            body = re.sub(rf"\s+{kw}\s*$", "", body, flags=re.IGNORECASE)
        return body.strip()

    def _col_list(self, body: str) -> Optional[str]:
        """Return the lowercased column list from a PK/UK body, or None."""
        m = re.search(r"\(([^)]+)\)", body)
        return self._clean_cols(m.group(1)) if m else None

    def _build_fk_alter(
        self, con_name: str, fk_body: str, src_table: str
    ) -> str:
        """
        Build the ``ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY`` statement.
        """
        m = re.match(
            r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+"
            # referenced table: optional schema prefix, quoted or unquoted
            r"((?:\"[^\"]+\"\s*\.\s*)?\"[^\"]+\"|"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?[A-Za-z0-9_$#]+)"
            r"\s*\(([^)]+)\)"
            r"(\s+ON\s+DELETE\s+(?:CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION))?",
            fk_body, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            logger.warning("Cannot parse FK body: %.120s", fk_body)
            return ""

        src_cols   = self._clean_cols(m.group(1))
        ref_table  = self._raw_name(m.group(2))
        ref_cols   = self._clean_cols(m.group(3))
        on_delete  = (m.group(4) or "").strip()
        on_del_cl  = f"\n    {on_delete}" if on_delete else ""

        return (
            f"ALTER TABLE {self.schema}.{src_table}\n"
            f"    ADD CONSTRAINT {con_name}\n"
            f"    FOREIGN KEY ({src_cols})\n"
            f"    REFERENCES {self.schema}.{ref_table} ({ref_cols}){on_del_cl};"
        )

    def _bare_pk(self, defn: str) -> str:
        """Convert a nameless ``PRIMARY KEY (…)`` clause."""
        m = re.search(r"\(([^)]+)\)", defn)
        return f"PRIMARY KEY ({self._clean_cols(m.group(1))})" if m else defn

    # ================================================================== #
    # CREATE INDEX                                                         #
    # ================================================================== #

    def _convert_create_index(self, stmt: str) -> Optional[str]:
        """
        Convert ``CREATE [UNIQUE] INDEX … ON …(…)`` to PostgreSQL.
        BITMAP indexes become plain indexes; storage options are dropped.
        """
        m = re.match(
            r"CREATE\s+(UNIQUE\s+)?(BITMAP\s+)?INDEX\s+"
            r"((?:\"[^\"]+\"\s*\.\s*)?\"[^\"]+\"|"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?[A-Za-z0-9_$#]+)"
            r"\s+ON\s+"
            r"((?:\"[^\"]+\"\s*\.\s*)?\"[^\"]+\"|"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?[A-Za-z0-9_$#]+)"
            r"\s*\(",
            stmt, re.IGNORECASE,
        )
        if not m:
            logger.warning("Cannot parse index statement: %.100s", stmt)
            return None

        unique     = "UNIQUE " if m.group(1) else ""
        idx_name   = self._raw_name(m.group(3))
        tbl_name   = self._raw_name(m.group(4))

        # The regex ends with \(, so m.end()-1 is the position of that (
        col_body = self._paren_body_at(stmt, m.end() - 1)
        if col_body is None:
            logger.warning("Cannot extract index columns for: %s", idx_name)
            return None

        columns = self._clean_index_cols(col_body)

        return (
            f"CREATE {unique}INDEX IF NOT EXISTS {idx_name}\n"
            f"    ON {self.schema}.{tbl_name} ({columns});"
        )

    # ── Index column cleaner (preserves ASC/DESC/expressions) ─────────── #

    def _clean_index_cols(self, raw: str) -> str:
        """
        Unquote column names in an index column list while preserving
        ASC/DESC direction specifiers and simple function expressions.
        """
        parts: list[str] = []
        for token in self._top_level_split(raw):
            token = token.strip()
            # Unquote the column name (first quoted word) if present
            token = re.sub(r'"([A-Za-z0-9_$#]+)"', lambda mo: mo.group(1).lower(), token)
            # Lowercase plain identifiers (stop at space/bracket)
            token = re.sub(
                r"^([A-Za-z0-9_$#]+)",
                lambda mo: mo.group(1).lower(),
                token,
            )
            parts.append(token)
        return ", ".join(parts)

    # ================================================================== #
    # COMMENT ON                                                           #
    # ================================================================== #

    def _convert_comment(self, stmt: str) -> Optional[str]:
        """Convert Oracle ``COMMENT ON TABLE/COLUMN`` to PostgreSQL syntax."""
        stmt = stmt.rstrip(";").strip()

        # ── COMMENT ON TABLE ──────────────────────────────────────────── #
        m = re.match(
            r"COMMENT\s+ON\s+TABLE\s+"
            r"((?:\"[^\"]+\"\s*\.\s*)?\"[^\"]+\"|"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?[A-Za-z0-9_$#]+)"
            r"\s+IS\s+('(?:''|[^'])*')",
            stmt, re.IGNORECASE | re.DOTALL,
        )
        if m:
            tbl = self._raw_name(m.group(1))
            return f"COMMENT ON TABLE {self.schema}.{tbl} IS {m.group(2)};"

        # ── COMMENT ON COLUMN (fully-qualified, quoted) ───────────────── #
        m = re.match(
            r"COMMENT\s+ON\s+COLUMN\s+"
            r"(?:\"[^\"]+\"\s*\.\s*)?"         # optional schema (discarded)
            r"\"([^\"]+)\""                     # table name (quoted)
            r"\s*\.\s*"
            r"\"([^\"]+)\""                     # column name (quoted)
            r"\s+IS\s+('(?:''|[^'])*')",
            stmt, re.IGNORECASE | re.DOTALL,
        )
        if m:
            tbl = m.group(1).lower()
            col = m.group(2).lower()
            return f"COMMENT ON COLUMN {self.schema}.{tbl}.{col} IS {m.group(3)};"

        # ── COMMENT ON COLUMN (unquoted identifiers) ──────────────────── #
        m = re.match(
            r"COMMENT\s+ON\s+COLUMN\s+"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?"    # optional schema
            r"([A-Za-z0-9_$#]+)"               # table name
            r"\s*\.\s*"
            r"([A-Za-z0-9_$#]+)"               # column name
            r"\s+IS\s+('(?:''|[^'])*')",
            stmt, re.IGNORECASE | re.DOTALL,
        )
        if m:
            tbl = m.group(1).lower()
            col = m.group(2).lower()
            return f"COMMENT ON COLUMN {self.schema}.{tbl}.{col} IS {m.group(3)};"

        logger.warning("Cannot parse COMMENT statement: %.100s", stmt)
        return None

    # ================================================================== #
    # ALTER TABLE (standalone)                                             #
    # ================================================================== #

    def _convert_alter_table(
        self, stmt: str
    ) -> tuple[list[str], list[str]]:
        """
        Convert a standalone ``ALTER TABLE`` statement.

        Only ``ADD CONSTRAINT … FOREIGN KEY`` produces output; all Oracle-only
        clauses (ENABLE ROW MOVEMENT, SUPPLEMENTAL LOG, etc.) are silently
        skipped.
        """
        _SKIP = [
            r"\bENABLE\s+ROW\s+MOVEMENT\b",
            r"\bDISABLE\s+ROW\s+MOVEMENT\b",
            r"\bSUPPLEMENTAL\s+LOG\b",
            r"\bENABLE\s+CONSTRAINT\b",
            r"\bDISABLE\s+CONSTRAINT\b",
            r"\bSHRINK\s+SPACE\b",
            r"\bMOVE\b",
        ]
        for pat in _SKIP:
            if re.search(pat, stmt, re.IGNORECASE):
                return [], []

        tbl_m = re.match(
            r"ALTER\s+TABLE\s+"
            r"((?:\"[^\"]+\"\s*\.\s*)?\"[^\"]+\"|"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?[A-Za-z0-9_$#]+)",
            stmt, re.IGNORECASE,
        )
        if not tbl_m:
            return [], []

        tbl_name = self._raw_name(tbl_m.group(1))

        if re.search(r"\bFOREIGN\s+KEY\b", stmt, re.IGNORECASE):
            cm = re.search(
                r"CONSTRAINT\s+\"?([A-Za-z0-9_$#]+)\"?\s+(FOREIGN\s+KEY.*)",
                stmt, re.IGNORECASE | re.DOTALL,
            )
            if cm:
                con_name = cm.group(1).lower()
                fk_raw   = self._strip_constraint_options(cm.group(2).rstrip(";"))
                fk_sql   = self._build_fk_alter(con_name, fk_raw, tbl_name)
                return [], [fk_sql] if fk_sql else []

        return [], []

    # ================================================================== #
    # Shared utilities                                                     #
    # ================================================================== #

    def _first_table_name(self, content: str) -> str:
        """Extract the first table name from the file content."""
        m = re.search(
            r"CREATE\s+TABLE\s+"
            r"(?:\"[^\"]+\"\s*\.\s*)?\"?([A-Za-z0-9_$#]+)\"?",
            content, re.IGNORECASE,
        )
        return m.group(1).lower() if m else "unknown"

    def _object_name(self, stmt: str, after: str) -> str:
        """Extract the DDL object name following the given keyword."""
        pat = (
            rf"{after}\s+"
            r"((?:\"[^\"]+\"\s*\.\s*)?\"[^\"]+\"|"
            r"(?:[A-Za-z0-9_$#]+\s*\.\s*)?[A-Za-z0-9_$#]+)"
        )
        m = re.search(pat, stmt, re.IGNORECASE)
        if not m:
            raise ValueError(f"Cannot find name after {after!r} in: {stmt[:80]}")
        return self._raw_name(m.group(1))

    def _raw_name(self, raw: str) -> str:
        """Strip schema prefix, double-quotes; return lowercase identifier."""
        # Remove optional schema prefix (quoted or unquoted)
        raw = re.sub(r'^"[^"]+"\s*\.\s*', "", raw.strip())
        raw = re.sub(r"^[A-Za-z0-9_$#]+\s*\.\s*", "", raw)
        return raw.strip().strip('"').lower()

    def _paren_body(self, stmt: str) -> Optional[str]:
        """Return the content between the first outermost `(` … `)` in stmt."""
        for i, ch in enumerate(stmt):
            if ch == "(":
                return self._paren_body_at(stmt, i)
        return None

    def _paren_body_at(self, stmt: str, start: int) -> Optional[str]:
        """Extract the paren body whose opening `(` is at index *start*."""
        depth = 0
        inner_start: Optional[int] = None
        for i in range(start, len(stmt)):
            ch = stmt[i]
            if ch == "(":
                if depth == 0:
                    inner_start = i + 1
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and inner_start is not None:
                    return stmt[inner_start:i]
        return None

    def _top_level_split(self, body: str) -> list[str]:
        """
        Split *body* on commas that are at parenthesis-depth 0,
        respecting single-quoted strings.
        """
        parts    : list[str] = []
        current  : list[str] = []
        depth    = 0
        in_quote = False

        for ch in body:
            if ch == "'" and not in_quote:
                in_quote = True
                current.append(ch)
            elif ch == "'" and in_quote:
                in_quote = False
                current.append(ch)
            elif in_quote:
                current.append(ch)
            elif ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(ch)

        last = "".join(current).strip()
        if last:
            parts.append(last)
        return parts

    def _clean_cols(self, raw: str) -> str:
        """Unquote and lowercase a comma-separated list of column names."""
        cols = [c.strip().strip('"').lower() for c in raw.split(",")]
        return ", ".join(c for c in cols if c)
