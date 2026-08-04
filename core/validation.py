"""
core/validation.py — Phase 2 Validation Engine (Section 4).

Deterministic, rule-based, pluggable — no agent involved. Section 9 is
explicit that Phase 2 is "full validation engine, masking engine with
FK-aware deterministic strategy, no agent yet — plain deterministic
pipeline, CLI-driven."

Covers: primary keys, foreign keys, unique constraints, data types (via
length/format checks — full type-coercion checking is adapter-specific and
deferred to the connector layer), length/precision, regex/format,
mandatory (NOT NULL) columns, lookup/reference-table membership, and
pluggable custom business rules (subclass `ValidationRule`).

Every run produces a `ValidationReport` — this is the structured artifact
the future Correction Agent and the UI will both consume (Section 4), so
its shape is deliberately UI-friendly (row/column level, human-readable
message, severity) rather than just a pass/fail boolean.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.database_port import TableMetadata
from core.glossary import Glossary
from core.schema_graph import SchemaGraph


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    rule: str
    severity: Severity
    row_index: int
    column: Optional[str]
    message: str
    value: Any = None


@dataclass
class ValidationReport:
    table: str
    total_rows: int
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def is_valid(self) -> bool:
        """Pre-load gate: warnings don't block, errors do (Section 6's
        'validation errors/warnings' preview keeps both, but only errors
        should stop SQL generation)."""
        return len(self.errors) == 0

    def summary(self) -> dict:
        return {
            "table": self.table,
            "total_rows": self.total_rows,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "is_valid": self.is_valid,
        }

    def human_readable(self, max_lines: int = 200) -> str:
        lines = [f"Validation report for {self.table}: {self.total_rows} row(s)"]
        lines.append(f"  {len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        for issue in self.issues[:max_lines]:
            loc = f"row {issue.row_index}" + (f", column {issue.column}" if issue.column else "")
            lines.append(f"  [{issue.severity.value.upper()}] {loc}: {issue.message} ({issue.rule})")
        if len(self.issues) > max_lines:
            lines.append(f"  ... and {len(self.issues) - max_lines} more (truncated for readability)")
        return "\n".join(lines)


@dataclass
class ValidationContext:
    schema_graph: Optional[SchemaGraph] = None
    glossary: Optional[Glossary] = None
    # table_name (lowercase) -> set of PK-column-value tuples already known to exist
    # in the target (e.g. loaded once from the target DB before validating a batch).
    existing_keys: dict[str, set[tuple]] = field(default_factory=dict)


class ValidationRule(ABC):
    """Base class for a pluggable rule (Section 4: 'user-defined custom rules
    (pluggable rule classes)'). Subclass this for anything business-specific,
    e.g. 'a terminated provider must have P_ENROL_STAT_TB.status = TRM'."""

    name: str = "base_rule"
    severity: Severity = Severity.ERROR

    @abstractmethod
    def check(
        self, rows: list[dict], table: TableMetadata, context: ValidationContext
    ) -> list[ValidationIssue]: ...


class NotNullRule(ValidationRule):
    name = "not_null"
    severity = Severity.ERROR

    def check(self, rows, table, context):
        issues = []
        required_cols = [c.name for c in table.columns if not c.nullable]
        for i, row in enumerate(rows):
            for col in required_cols:
                if row.get(col) in (None, ""):
                    issues.append(
                        ValidationIssue(self.name, self.severity, i, col,
                                         f"{col} is required (NOT NULL) but missing", row.get(col))
                    )
        return issues


class PrimaryKeyRule(ValidationRule):
    """PK present + unique within the batch (cross-batch uniqueness needs
    `context.existing_keys`, which this also checks when supplied)."""

    name = "primary_key"
    severity = Severity.ERROR

    def check(self, rows, table, context):
        issues = []
        if not table.primary_key:
            return issues
        existing = context.existing_keys.get(table.name.lower(), set())
        seen: set[tuple] = set()
        for i, row in enumerate(rows):
            key = tuple(row.get(c) for c in table.primary_key)
            if any(v in (None, "") for v in key):
                issues.append(
                    ValidationIssue(self.name, self.severity, i, ",".join(table.primary_key),
                                     "primary key value(s) missing", key)
                )
                continue
            if key in seen:
                issues.append(
                    ValidationIssue(self.name, self.severity, i, ",".join(table.primary_key),
                                     f"duplicate primary key {key} within this batch", key)
                )
            elif key in existing:
                issues.append(
                    ValidationIssue(self.name, self.severity, i, ",".join(table.primary_key),
                                     f"primary key {key} already exists in target", key)
                )
            seen.add(key)
        return issues


class UniqueRule(ValidationRule):
    name = "unique"
    severity = Severity.ERROR

    def __init__(self, columns: list[str]) -> None:
        self.columns = columns

    def check(self, rows, table, context):
        issues = []
        seen: set[tuple] = set()
        for i, row in enumerate(rows):
            key = tuple(row.get(c) for c in self.columns)
            if key in seen:
                issues.append(
                    ValidationIssue(self.name, self.severity, i, ",".join(self.columns),
                                     f"duplicate value for unique constraint {self.columns}: {key}", key)
                )
            seen.add(key)
        return issues


class ForeignKeyRule(ValidationRule):
    """Checks every FK column resolves to a known parent key, either within
    `context.existing_keys` (typically pre-loaded from the target) or —
    for self-referencing / same-load-batch FKs like P_AFFL_TB — against
    parent rows present in the SAME batch when the parent table is passed
    via `context.existing_keys[parent_table] |= {batch-derived keys}`
    by the caller before validating children. Silent (no issue) when the
    parent's key set wasn't supplied, since that's a caller-scoping choice,
    not a data error."""

    name = "foreign_key"
    severity = Severity.ERROR

    def check(self, rows, table, context):
        issues = []
        for fk in table.foreign_keys:
            parent_keys = context.existing_keys.get(fk.parent_table.lower())
            if parent_keys is None:
                continue
            for i, row in enumerate(rows):
                values = tuple(row.get(c) for c in fk.child_columns)
                if any(v is None for v in values):
                    continue  # nullable FK; NotNullRule covers required-ness separately
                if values not in parent_keys:
                    issues.append(
                        ValidationIssue(
                            self.name, self.severity, i, ",".join(fk.child_columns),
                            f"FK {fk.fk_name} -> {fk.parent_table} has no matching parent key {values}",
                            values,
                        )
                    )
        return issues


class LengthRule(ValidationRule):
    """Length/precision check against VARCHAR(n)/CHAR(n) as reported by
    introspection. Warning-level: an oversized value is very likely a
    load-time truncation error, but some connectors silently truncate
    rather than reject, so this stays a warning unless the caller escalates it."""

    name = "length"
    severity = Severity.WARNING
    _len_re = re.compile(r"\((\d+)")

    def check(self, rows, table, context):
        issues = []
        limits = {}
        for c in table.columns:
            if c.data_type.upper().startswith(("VARCHAR", "CHAR")):
                m = self._len_re.search(c.data_type)
                if m:
                    limits[c.name] = int(m.group(1))
        for i, row in enumerate(rows):
            for col, limit in limits.items():
                val = row.get(col)
                if isinstance(val, str) and len(val) > limit:
                    issues.append(
                        ValidationIssue(self.name, self.severity, i, col,
                                         f"{col} exceeds max length {limit} (got {len(val)})", val)
                    )
        return issues


class RegexRule(ValidationRule):
    name = "regex"
    severity = Severity.ERROR

    def __init__(self, column: str, pattern: str, description: str = "") -> None:
        self.column = column
        self.pattern = re.compile(pattern)
        self.description = description or f"must match /{pattern}/"

    def check(self, rows, table, context):
        issues = []
        for i, row in enumerate(rows):
            val = row.get(self.column)
            if val in (None, ""):
                continue
            if not self.pattern.fullmatch(str(val)):
                issues.append(
                    ValidationIssue(self.name, self.severity, i, self.column,
                                     f"{self.column} {self.description}", val)
                )
        return issues


class LookupRule(ValidationRule):
    """Reference/lookup-table membership check (Section 4), backed by the
    Glossary's `reference_data.csv`-derived valid-value categories
    (e.g. category='Provider Status', valid codes ACT/INA/PEN/TRM/SUS/REV)."""

    name = "lookup"
    severity = Severity.ERROR

    def __init__(self, column: str, category: str) -> None:
        self.column = column
        self.category = category

    def check(self, rows, table, context):
        issues = []
        if not context.glossary:
            return issues
        for i, row in enumerate(rows):
            val = row.get(self.column)
            if val in (None, ""):
                continue
            if not context.glossary.is_valid_code(self.category, str(val)):
                issues.append(
                    ValidationIssue(self.name, self.severity, i, self.column,
                                     f"{val!r} is not a valid {self.category} code", val)
                )
        return issues


class ValidationEngine:
    def __init__(self, rules: Optional[list[ValidationRule]] = None) -> None:
        self.rules: list[ValidationRule] = rules or []

    def register(self, rule: ValidationRule) -> "ValidationEngine":
        self.rules.append(rule)
        return self

    def validate_batch(
        self, rows: list[dict], table: TableMetadata, context: Optional[ValidationContext] = None
    ) -> ValidationReport:
        context = context or ValidationContext()
        report = ValidationReport(table=table.name, total_rows=len(rows))
        for rule in self.rules:
            report.issues.extend(rule.check(rows, table, context))
        return report

    @staticmethod
    def default_rules_for(table: TableMetadata) -> list[ValidationRule]:
        """Auto-builds the structural rules straight from introspected
        TableMetadata — the Section 4 checks that don't need hand-authoring
        (NOT NULL, PK, length, FK-if-present). Lookup/regex/custom rules are
        business-specific and must be registered explicitly."""
        rules: list[ValidationRule] = [NotNullRule(), PrimaryKeyRule(), LengthRule()]
        if table.foreign_keys:
            rules.append(ForeignKeyRule())
        return rules
