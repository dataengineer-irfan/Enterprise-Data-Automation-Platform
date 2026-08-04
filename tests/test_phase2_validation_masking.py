"""
tests/test_phase2_validation_masking.py — Phase 2 smoke tests.

Covers the Validation Engine (Section 4) and the FK-aware Masking Engine
(Section 5), including the referential-integrity guarantee that's the
whole point of Section 5: masking a parent column and a child FK column
with the same deterministic strategy/salt must produce byte-identical
output so joins still resolve after masking.

Run: python -m pytest tests/test_phase2_validation_masking.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.oracle_ddl_adapter import OracleDDLAdapter
from core.database_port import ColumnMetadata, ForeignKeyMetadata, TableMetadata
from core.glossary import load_default_glossary
from core.masking import (
    MaskingEngine,
    MaskingPolicy,
    MaskingRule,
    MaskStrategy,
    SensitivityClassifier,
    masking_coverage,
)
from core.rules_config import load_sensitive_columns
from core.schema_graph import load_default_schema_graph
from core.validation import (
    ForeignKeyRule,
    NotNullRule,
    PrimaryKeyRule,
    RegexRule,
    ValidationContext,
    ValidationEngine,
    ValidationReport,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"


def _load_table(name: str) -> TableMetadata:
    graph = load_default_schema_graph(CONFIG_DIR)
    adapter = OracleDDLAdapter(DDL_DIR, graph)
    adapter.connect()
    tables = {t.name.lower(): t for t in adapter.introspect_schema("provider")}
    return tables[name.lower()]


# ─────────────────────────────────────────────────────────────────────────── #
# Validation Engine                                                           #
# ─────────────────────────────────────────────────────────────────────────── #


def test_primary_key_rule_flags_missing_and_duplicate_keys():
    table = _load_table("p_alt_id_tb")
    rows = [
        {"p_sys_id": "900001", "p_alt_id_sk": "1", "p_alt_id": "123456789"},
        {"p_sys_id": "900001", "p_alt_id_sk": "1", "p_alt_id": "duplicate-pk"},  # dup
        {"p_sys_id": "", "p_alt_id_sk": "2", "p_alt_id": "missing-pk"},         # missing
    ]
    report = ValidationEngine([PrimaryKeyRule()]).validate_batch(rows, table)
    assert not report.is_valid
    assert len(report.errors) == 2


def test_not_null_and_pk_pass_on_clean_data():
    table = _load_table("p_alt_id_tb")
    rows = [
        {"p_sys_id": "900001", "p_alt_id_sk": "1", "p_alt_id": "123456789", "p_alt_id_ty_cd": "SY"},
        {"p_sys_id": "900001", "p_alt_id_sk": "2", "p_alt_id": "1234567890", "p_alt_id_ty_cd": "XX"},
    ]
    report = ValidationEngine(ValidationEngine.default_rules_for(table)).validate_batch(rows, table)
    assert report.is_valid


def test_foreign_key_rule_flags_orphan_when_parent_keys_supplied():
    table = _load_table("p_alt_id_tb")  # has FK -> p_dtl_tb via p_sys_id
    rows = [
        {"p_sys_id": "900999", "p_alt_id_sk": "1", "p_alt_id": "123456789"},  # orphan
    ]
    context = ValidationContext(existing_keys={"p_dtl_tb": {("900001",)}})
    report = ValidationEngine([ForeignKeyRule()]).validate_batch(rows, table, context)
    assert not report.is_valid
    assert report.errors[0].rule == "foreign_key"


def test_foreign_key_rule_silent_when_parent_keys_not_supplied():
    """Caller-scoping choice, not a data error (see ForeignKeyRule docstring)."""
    table = _load_table("p_alt_id_tb")
    rows = [{"p_sys_id": "900999", "p_alt_id_sk": "1", "p_alt_id": "123456789"}]
    report = ValidationEngine([ForeignKeyRule()]).validate_batch(rows, table, ValidationContext())
    assert report.is_valid


def test_lookup_rule_uses_reference_data_csv():
    glossary = load_default_glossary(CONFIG_DIR)
    table = TableMetadata(name="dummy", schema="provider", columns=[ColumnMetadata("status_cd", "VARCHAR(3)")])
    rows = [{"status_cd": "ACT"}, {"status_cd": "BOGUS"}]
    from core.validation import LookupRule

    context = ValidationContext(glossary=glossary)
    report = ValidationEngine([LookupRule("status_cd", "Provider Status")]).validate_batch(rows, table, context)
    assert len(report.errors) == 1
    assert "BOGUS" in report.errors[0].message


def test_report_human_readable_and_summary():
    table = _load_table("p_alt_id_tb")
    rows = [{"p_sys_id": "", "p_alt_id_sk": "1", "p_alt_id": "x"}]
    report = ValidationEngine([PrimaryKeyRule()]).validate_batch(rows, table)
    text = report.human_readable()
    assert "ERROR" in text
    summary = report.summary()
    assert summary["is_valid"] is False
    assert summary["error_count"] == 1


# ─────────────────────────────────────────────────────────────────────────── #
# Masking Engine                                                              #
# ─────────────────────────────────────────────────────────────────────────── #


def test_deterministic_masking_is_stable_across_calls():
    engine = MaskingEngine()
    rule = MaskingRule(column="p_alt_id", strategy=MaskStrategy.DETERMINISTIC, salt="s1")
    a = engine.mask_value("123456789", rule)
    b = engine.mask_value("123456789", rule)
    assert a == b
    assert a != "123456789"


def test_deterministic_masking_differs_by_salt():
    engine = MaskingEngine()
    r1 = MaskingRule(column="x", strategy=MaskStrategy.DETERMINISTIC, salt="salt-a")
    r2 = MaskingRule(column="x", strategy=MaskStrategy.DETERMINISTIC, salt="salt-b")
    assert engine.mask_value("value", r1) != engine.mask_value("value", r2)


def test_format_preserving_keeps_digit_shape():
    engine = MaskingEngine()
    rule = MaskingRule(column="ssn", strategy=MaskStrategy.FORMAT_PRESERVING, salt="s1")
    masked = engine.mask_value("123-45-6789", rule)
    assert len(masked) == len("123-45-6789")
    assert masked[3] == "-" and masked[6] == "-"
    assert masked.replace("-", "").isdigit()


def test_nullify_and_static_strategies():
    engine = MaskingEngine()
    assert engine.mask_value("secret", MaskingRule("c", MaskStrategy.NULLIFY, "s")) is None
    assert engine.mask_value("secret", MaskingRule("c", MaskStrategy.STATIC, "s", static_value="REDACTED")) == "REDACTED"


def test_synthetic_masking_is_deterministic_and_faker_backed():
    engine = MaskingEngine()
    rule = MaskingRule(column="name", strategy=MaskStrategy.SYNTHETIC, salt="s1", faker_provider="first_name")
    a = engine.mask_value("Alice", rule)
    b = engine.mask_value("Alice", rule)
    assert a == b
    assert isinstance(a, str) and len(a) > 0


def test_fk_propagation_makes_child_and_parent_masks_match():
    """The core Section 5 guarantee: mask p_dtl_tb.p_sys_id deterministically,
    propagate to p_alt_id_tb.p_sys_id (its FK), then confirm the SAME source
    value masks to the SAME output in both tables — i.e. the join still resolves."""
    p_dtl_tb = _load_table("p_dtl_tb")
    p_alt_id_tb = _load_table("p_alt_id_tb")

    parent_policy = MaskingPolicy(name="parent")
    parent_policy.set_rule(MaskingRule("p_sys_id", MaskStrategy.DETERMINISTIC, salt="shared-salt"))

    child_policy = MaskingPolicy(name="child")
    engine = MaskingEngine()
    propagated = engine.propagate_fk_rules(
        child_policy, p_alt_id_tb, all_policies={"p_dtl_tb": parent_policy}
    )
    assert "p_sys_id" in propagated

    sample_id = "900001"
    parent_masked = engine.mask_value(sample_id, parent_policy.get_rule("p_sys_id"))
    child_masked = engine.mask_value(sample_id, child_policy.get_rule("p_sys_id"))
    assert parent_masked == child_masked  # <- the whole point of Section 5

    problems = engine.verify_fk_consistency([(p_dtl_tb, parent_policy), (p_alt_id_tb, child_policy)])
    assert problems == []


def test_verify_fk_consistency_catches_divergent_salt():
    p_dtl_tb = _load_table("p_dtl_tb")
    p_alt_id_tb = _load_table("p_alt_id_tb")

    parent_policy = MaskingPolicy(name="parent")
    parent_policy.set_rule(MaskingRule("p_sys_id", MaskStrategy.DETERMINISTIC, salt="salt-A"))

    child_policy = MaskingPolicy(name="child")
    child_policy.set_rule(MaskingRule("p_sys_id", MaskStrategy.DETERMINISTIC, salt="salt-B"))  # deliberately wrong

    engine = MaskingEngine()
    problems = engine.verify_fk_consistency([(p_dtl_tb, parent_policy), (p_alt_id_tb, child_policy)])
    assert len(problems) == 1
    assert "diverges" in problems[0]


def test_sensitivity_classifier_uses_explicit_flags_and_heuristics():
    explicit = load_sensitive_columns(CONFIG_DIR)
    assert "p_alt_id" in explicit
    classifier = SensitivityClassifier(sensitive_columns=explicit)
    assert classifier.is_sensitive("p_alt_id")           # explicit config flag
    assert classifier.is_sensitive("home_phone_num")      # name heuristic
    assert not classifier.is_sensitive("p_alt_id_ty_cd")


def test_masking_coverage_reflects_rule_completeness():
    table = _load_table("p_alt_id_tb")
    classifier = SensitivityClassifier(sensitive_columns=load_sensitive_columns(CONFIG_DIR))

    empty_policy = MaskingPolicy(name="empty")
    assert masking_coverage(table, empty_policy, classifier) == 0.0

    full_policy = MaskingPolicy(name="full")
    for col in classifier.classify_table(table):
        full_policy.set_rule(MaskingRule(col, MaskStrategy.DETERMINISTIC, salt="s"))
    assert masking_coverage(table, full_policy, classifier) == 1.0


def test_bulk_pattern_application():
    table = _load_table("p_alt_id_tb")
    policy = MaskingPolicy(name="bulk")
    matched = policy.apply_bulk_pattern(table, "*_ty_cd", MaskStrategy.NULLIFY, salt="s")
    assert "p_alt_id_ty_cd" in matched
    assert policy.get_rule("p_alt_id_ty_cd").strategy == MaskStrategy.NULLIFY


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
