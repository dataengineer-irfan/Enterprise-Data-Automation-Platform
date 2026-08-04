"""
core/masking.py — Phase 2 Masking Engine (Section 5).

Referential-integrity-safe, deterministic pipeline — no agent yet
(Section 9 Phase 2). Strategies: static, random ("dynamic" in the spec's
wording — re-randomized per call, explicitly NOT stable across runs),
deterministic (HMAC-based), format-preserving (digit/letter-shape
preserving, deterministic), shuffle (seeded, deterministic), nullify,
synthetic (Faker-based, seeded off the same HMAC so it's deterministic too).

FK-aware guarantee: `MaskingEngine.propagate_fk_rules()` copies a parent
column's exact rule (same strategy + same salt) onto every FK column that
references it, so joins still resolve after masking. This class also
implements `verify_fk_consistency()`, which stands in for "independently
re-checked by the Validation Agent" (Section 5) until the agent layer
exists (Phase 3/4) — call it as a CI/CLI gate in the meantime.
"""
from __future__ import annotations

import hashlib
import hmac
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from faker import Faker

from core.database_port import TableMetadata
from core.schema_graph import SchemaGraph


class MaskStrategy(str, Enum):
    STATIC = "static"
    RANDOM = "random"
    DETERMINISTIC = "deterministic"
    FORMAT_PRESERVING = "format_preserving"
    SHUFFLE = "shuffle"
    NULLIFY = "nullify"
    SYNTHETIC = "synthetic"


# Name-heuristic half of `classify_sensitivity` (Section 5). The other half
# is the explicit `sensitive: true` list pulled from
# config/generation_rules.yaml (SensitivityClassifier.explicit).
_NAME_HEURISTICS = re.compile(
    r"(SSN|TIN|TAX_ID|DEA|NPI|PASSWORD|PASSWD|CREDIT|CARD_NUM|DOB|EMAIL|PHONE|LICENSE|LIC_CERT)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MaskingRule:
    column: str
    strategy: MaskStrategy
    salt: str
    faker_provider: Optional[str] = None  # e.g. "ssn", "phone_number" — used by SYNTHETIC
    static_value: Optional[str] = None    # used by STATIC


@dataclass
class MaskingPolicy:
    """A named, versioned rule set (Section 7a: 'provider_schema_v3')."""

    name: str
    rules: dict[str, MaskingRule] = field(default_factory=dict)  # column -> rule

    def set_rule(self, rule: MaskingRule) -> None:
        self.rules[rule.column] = rule

    def get_rule(self, column: str) -> Optional[MaskingRule]:
        return self.rules.get(column)

    def apply_bulk_pattern(
        self, table: TableMetadata, name_pattern: str, strategy: MaskStrategy, salt: str,
        faker_provider: Optional[str] = None,
    ) -> list[str]:
        """Section 7a bulk rule application: apply one strategy to every
        column matching a glob-ish pattern (e.g. '*_SSN', '*_TIN') on one
        table in a single call. Returns the column names that matched."""
        regex = re.compile("^" + re.escape(name_pattern).replace(r"\*", ".*") + "$", re.IGNORECASE)
        matched = [c.name for c in table.columns if regex.match(c.name)]
        for col in matched:
            self.set_rule(MaskingRule(column=col, strategy=strategy, salt=salt, faker_provider=faker_provider))
        return matched


class SensitivityClassifier:
    """`classify_sensitivity` (Section 5): name heuristic + explicit
    glossary/config flags (the `sensitive: true` entries already present in
    config/generation_rules.yaml for p_alt_id_ssn_tin / p_owner_ssn_num)."""

    def __init__(self, sensitive_columns: Optional[set[str]] = None) -> None:
        self.explicit: set[str] = {c.lower() for c in (sensitive_columns or set())}

    def is_sensitive(self, column_name: str) -> bool:
        return column_name.lower() in self.explicit or bool(_NAME_HEURISTICS.search(column_name))

    def classify_table(self, table: TableMetadata) -> list[str]:
        return [c.name for c in table.columns if self.is_sensitive(c.name)]


def _hmac_hex(value: str, salt: str) -> str:
    return hmac.new(salt.encode(), str(value).encode(), hashlib.sha256).hexdigest()


class MaskingEngine:
    def __init__(self, faker_locale: str = "en_US") -> None:
        self._faker = Faker(faker_locale)

    # -- FK propagation --------------------------------------------------------
    def propagate_fk_rules(
        self, policy: MaskingPolicy, table: TableMetadata, all_policies: dict[str, MaskingPolicy]
    ) -> list[str]:
        """
        For every FK on `table` whose parent table already has a masking
        rule on the referenced column (in `all_policies[parent_table.lower()]`),
        copy that EXACT rule (same strategy + same salt) onto this table's
        FK column — so a deterministic/synthetic mask on the parent value
        produces byte-identical output on the child FK value, and the join
        still resolves post-masking. Returns the list of columns that were
        auto-populated this way.
        """
        propagated = []
        for fk in table.foreign_keys:
            parent_policy = all_policies.get(fk.parent_table.lower())
            if not parent_policy:
                continue
            for child_col, parent_col in zip(fk.child_columns, fk.parent_columns):
                parent_rule = parent_policy.get_rule(parent_col)
                if parent_rule and not policy.get_rule(child_col):
                    policy.set_rule(
                        MaskingRule(
                            column=child_col,
                            strategy=parent_rule.strategy,
                            salt=parent_rule.salt,
                            faker_provider=parent_rule.faker_provider,
                            static_value=parent_rule.static_value,
                        )
                    )
                    propagated.append(child_col)
        return propagated

    def verify_fk_consistency(
        self, tables_and_policies: list[tuple[TableMetadata, MaskingPolicy]]
    ) -> list[str]:
        """Returns human-readable problems (empty = clean). This is the
        deterministic-pipeline stand-in for Section 5's 'independently
        re-checked by the Validation Agent before any SQL is generated.'"""
        problems: list[str] = []
        by_table = {t.name.lower(): (t, p) for t, p in tables_and_policies}
        for table, policy in tables_and_policies:
            for fk in table.foreign_keys:
                parent = by_table.get(fk.parent_table.lower())
                if not parent:
                    continue
                _, parent_policy = parent
                for child_col, parent_col in zip(fk.child_columns, fk.parent_columns):
                    child_rule = policy.get_rule(child_col)
                    parent_rule = parent_policy.get_rule(parent_col)
                    if not child_rule or not parent_rule:
                        continue
                    if child_rule.strategy != parent_rule.strategy or child_rule.salt != parent_rule.salt:
                        problems.append(
                            f"{table.name}.{child_col} masking "
                            f"({child_rule.strategy.value}/{child_rule.salt}) diverges from parent "
                            f"{fk.parent_table}.{parent_col} ({parent_rule.strategy.value}/{parent_rule.salt})"
                            " — joins will break after masking."
                        )
        return problems

    # -- masking a single value --------------------------------------------------------
    def mask_value(self, value: Any, rule: MaskingRule) -> Any:
        if value is None:
            return None
        s = str(value)

        if rule.strategy == MaskStrategy.NULLIFY:
            return None
        if rule.strategy == MaskStrategy.STATIC:
            return rule.static_value if rule.static_value is not None else "***"
        if rule.strategy == MaskStrategy.RANDOM:
            return _hmac_hex(f"{s}{random.random()}", rule.salt)[: max(len(s), 8)]
        if rule.strategy == MaskStrategy.DETERMINISTIC:
            return _hmac_hex(s, rule.salt)
        if rule.strategy == MaskStrategy.FORMAT_PRESERVING:
            return self._format_preserving(s, rule.salt)
        if rule.strategy == MaskStrategy.SHUFFLE:
            chars = list(s)
            rnd = random.Random(_hmac_hex(s, rule.salt))
            rnd.shuffle(chars)
            return "".join(chars)
        if rule.strategy == MaskStrategy.SYNTHETIC:
            seed_hex = _hmac_hex(s, rule.salt)[:8]
            self._faker.seed_instance(int(seed_hex, 16))
            provider = rule.faker_provider or "word"
            return str(getattr(self._faker, provider)())
        raise ValueError(f"Unknown masking strategy: {rule.strategy}")

    @staticmethod
    def _format_preserving(s: str, salt: str) -> str:
        """Deterministic, digit/letter-shape-preserving substitution.

        This is a lightweight stand-in, not real FF1/FF3-1 format-preserving
        encryption — sufficient for Phase 2's deterministic CLI pipeline
        (same input+salt always -> same output, non-reversible without the
        salt) but NOT cryptographically reviewed FPE. Swap in a vetted
        library (e.g. `pyffx`) before treating this as encryption rather
        than masking. Documented as a known limitation in ADR-0002.
        """
        digest = _hmac_hex(s, salt)
        out = []
        for i, ch in enumerate(s):
            h = int(digest[i % len(digest)], 16)
            if ch.isdigit():
                out.append(str(h % 10))
            elif ch.isupper():
                out.append(chr(ord("A") + (h % 26)))
            elif ch.islower():
                out.append(chr(ord("a") + (h % 26)))
            else:
                out.append(ch)
        return "".join(out)

    # -- masking a batch --------------------------------------------------------
    def apply_masking_dry_run(self, rows: list[dict], policy: MaskingPolicy, limit: int = 20) -> list[dict]:
        """Preview only — no mutation (Section 6: 'masking preview
        (before/after)'; Section 7a: dry-run toggle usable independent of a schedule)."""
        preview = []
        for row in rows[:limit]:
            before = {col: row.get(col) for col in policy.rules}
            after = {col: self.mask_value(row.get(col), rule) for col, rule in policy.rules.items()}
            preview.append({"before": before, "after": after})
        return preview

    def apply_masking(self, rows: list[dict], policy: MaskingPolicy) -> list[dict]:
        masked = []
        for row in rows:
            new_row = dict(row)
            for col, rule in policy.rules.items():
                if col in new_row:
                    new_row[col] = self.mask_value(new_row[col], rule)
            masked.append(new_row)
        return masked


def masking_coverage(table: TableMetadata, policy: MaskingPolicy, classifier: SensitivityClassifier) -> float:
    """Section 7a's 'masking-coverage %' pre-flight metric: fraction of
    classified-sensitive columns actually covered by a rule in `policy`."""
    sensitive_cols = classifier.classify_table(table)
    if not sensitive_cols:
        return 1.0
    covered = sum(1 for c in sensitive_cols if policy.get_rule(c))
    return covered / len(sensitive_cols)
