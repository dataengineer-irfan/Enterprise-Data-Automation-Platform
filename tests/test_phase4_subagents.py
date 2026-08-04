"""
tests/test_phase4_subagents.py — unit tests for each Section 2.1 subagent
in isolation (condensed summary + shared-storage detail pointer contract).

Run: python -m pytest tests/test_phase4_subagents.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.oracle_ddl_adapter import OracleDDLAdapter
from agent.shared_storage import SharedStorage
from agent.subagents.correction_agent import CorrectionAgent
from agent.subagents.masking_agent import MaskingAgent
from agent.subagents.profiling_mapping_agent import ProfilingMappingAgent
from agent.subagents.schema_metadata_agent import SchemaMetadataAgent
from agent.subagents.sql_generation_agent import SQLGenerationAgent
from agent.subagents.validation_agent import ValidationAgent
from core.glossary import load_default_glossary
from core.masking import MaskStrategy
from core.rules_config import load_sensitive_columns
from core.schema_graph import load_default_schema_graph

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"
SAMPLE_CSV = ROOT / "samples" / "p_alt_id_tb.csv"


def _table(name: str):
    graph = load_default_schema_graph(CONFIG_DIR)
    adapter = OracleDDLAdapter(DDL_DIR, graph)
    adapter.connect()
    tables = {t.name.lower(): t for t in adapter.introspect_schema("provider")}
    return tables[name.lower()]


def _storage(tmp_path) -> SharedStorage:
    return SharedStorage(tmp_path / "shared")


# ─────────────────────────────────────────────────────────────────────────── #
# Schema/Metadata Agent                                                       #
# ─────────────────────────────────────────────────────────────────────────── #


def test_schema_metadata_agent_introspect_and_fk_graph(tmp_path):
    graph = load_default_schema_graph(CONFIG_DIR)
    glossary = load_default_glossary(CONFIG_DIR)
    adapter = OracleDDLAdapter(DDL_DIR, graph)
    adapter.connect()
    agent = SchemaMetadataAgent(_storage(tmp_path), adapter, graph, glossary)

    result = agent.run({"action": "introspect_schema", "table": "p_alt_id_tb"})
    assert result.success
    assert result.summary["pk"] == ["p_sys_id", "p_alt_id_sk"]

    fk_result = agent.run({"action": "build_fk_graph", "table": "p_alt_id_tb"})
    assert fk_result.success
    assert fk_result.summary["parent_count"] >= 1

    glossary_result = agent.run({"action": "resolve_glossary_term", "term": "NPI"})
    assert glossary_result.success
    assert glossary_result.summary["table"] == "PROVIDER"

    missing_result = agent.run({"action": "resolve_glossary_term", "term": "not-a-real-term"})
    assert not missing_result.success


# ─────────────────────────────────────────────────────────────────────────── #
# Profiling/Mapping Agent                                                     #
# ─────────────────────────────────────────────────────────────────────────── #


def test_profiling_agent_profile_and_diff(tmp_path):
    agent = ProfilingMappingAgent(_storage(tmp_path))
    table = _table("p_alt_id_tb")

    profile_result = agent.run({"action": "profile_file", "path": str(SAMPLE_CSV)})
    assert profile_result.success
    assert profile_result.summary["row_count"] == 3

    profile_detail = agent._storage.read_detail(profile_result.detail_pointer.pointer)
    diff_result = agent.run({"action": "diff_against_schema", "profile": profile_detail, "table": table})
    assert diff_result.success
    assert "missing_required_count" in diff_result.summary


def test_profiling_agent_column_mapping_confidence_levels(tmp_path):
    agent = ProfilingMappingAgent(_storage(tmp_path))
    table = _table("p_alt_id_tb")

    result = agent.run({
        "action": "suggest_column_mapping",
        "csv_columns": ["p_sys_id", "SSN_NUM", "totally_unrelated_garbage_field"],
        "table": table,
    })
    assert result.success
    detail = agent._storage.read_detail(result.detail_pointer.pointer)
    by_col = {d["csv_column"]: d for d in detail}
    assert by_col["p_sys_id"]["confidence"] == "high"  # exact match
    assert by_col["totally_unrelated_garbage_field"]["confidence"] == "none"


# ─────────────────────────────────────────────────────────────────────────── #
# Validation Agent (report only, never fixes)                                 #
# ─────────────────────────────────────────────────────────────────────────── #


def test_validation_agent_reports_condensed_summary(tmp_path):
    graph = load_default_schema_graph(CONFIG_DIR)
    glossary = load_default_glossary(CONFIG_DIR)
    agent = ValidationAgent(_storage(tmp_path), graph, glossary)
    table = _table("p_alt_id_tb")

    rows = [{"p_sys_id": "", "p_alt_id_sk": "1", "p_alt_id": "x"}]
    result = agent.run({"action": "validate_batch", "rows": rows, "table": table})
    assert not result.success is False or result.success  # result.success reflects dispatch success, not data validity
    assert result.summary["is_valid"] is False
    assert result.summary["error_count"] >= 1

    explain_result = agent.run({"action": "explain_violation", "issue": {"row": 0, "column": "p_sys_id", "message": "missing", "rule": "primary_key", "severity": "error"}})
    assert "Row 0" in explain_result.summary["explanation"]


# ─────────────────────────────────────────────────────────────────────────── #
# Correction/Suggestion Agent (proposes only, never applies)                  #
# ─────────────────────────────────────────────────────────────────────────── #


def test_correction_agent_proposes_phone_and_date_fixes_without_applying(tmp_path):
    agent = CorrectionAgent(_storage(tmp_path))

    phone_result = agent.run({"action": "suggest_correction", "column": "p_phone_num", "value": "5551234567"})
    assert phone_result.summary["applied"] is False
    assert phone_result.summary["after"] == "(555) 123-4567"

    date_result = agent.run({"action": "suggest_correction", "column": "p_beg_dt", "value": "01/15/2024"})
    assert date_result.summary["after"] == "2024-01-15"

    unfixable_result = agent.run({"action": "suggest_correction", "column": "p_random_col", "value": "already clean"})
    assert unfixable_result.summary["fixable"] is False


# ─────────────────────────────────────────────────────────────────────────── #
# Masking Agent (proposes strategy, enforces FK consistency via core.masking) #
# ─────────────────────────────────────────────────────────────────────────── #


def test_masking_agent_classify_and_propose(tmp_path):
    sensitive = load_sensitive_columns(CONFIG_DIR)
    agent = MaskingAgent(_storage(tmp_path), sensitive)
    table = _table("p_alt_id_tb")

    classify_result = agent.run({"action": "classify_sensitivity", "table": table})
    assert "p_alt_id" in classify_result.summary["sensitive_columns"]

    propose_result = agent.run({"action": "propose_masking_rule", "table": table})
    policy = propose_result.runtime_payload
    assert policy is not None
    assert policy.get_rule("p_alt_id").strategy == MaskStrategy.DETERMINISTIC

    import csv as csv_mod
    with open(SAMPLE_CSV, newline="") as fh:
        rows = list(csv_mod.DictReader(fh))
    dry_run_result = agent.run({"action": "apply_masking_dry_run", "rows": rows, "policy": policy})
    assert dry_run_result.summary["total_rows"] == 3


# ─────────────────────────────────────────────────────────────────────────── #
# SQL Generation Agent (text only — no write authority)                       #
# ─────────────────────────────────────────────────────────────────────────── #


def test_sql_generation_agent_produces_insert_and_rollback_scripts(tmp_path):
    agent = SQLGenerationAgent(_storage(tmp_path))
    table = _table("p_alt_id_tb")
    rows = [{"p_sys_id": "900001", "p_alt_id_sk": "1", "p_alt_id": "123456789"}]

    gen_result = agent.run({"action": "generate_sql", "table": table, "rows": rows})
    assert gen_result.success
    assert gen_result.summary["statement_count"] == 1
    assert "INSERT INTO provider.p_alt_id_tb" in gen_result.runtime_payload[0]

    estimate_result = agent.run({"action": "estimate_execution_time", "row_count": 5000})
    assert estimate_result.summary["estimated_seconds"] > 0

    rollback_result = agent.run({"action": "build_rollback_plan", "table": table, "rows": rows})
    assert "DELETE FROM provider.p_alt_id_tb" in rollback_result.runtime_payload[0]
    assert "900001" in rollback_result.runtime_payload[0]


def test_sql_generation_agent_generates_update_delete_and_upsert_sql(tmp_path):
    agent = SQLGenerationAgent(_storage(tmp_path))
    table = _table("p_alt_id_tb")
    rows = [{"p_sys_id": "900001", "p_alt_id_sk": "1", "p_alt_id": "123456789"}]

    update_result = agent.run({"action": "generate_sql", "table": table, "rows": rows, "operation": "update"})
    assert update_result.success
    assert "UPDATE provider.p_alt_id_tb" in update_result.runtime_payload[0]
    assert "SET p_alt_id = '123456789'" in update_result.runtime_payload[0]

    delete_result = agent.run({"action": "generate_sql", "table": table, "rows": rows, "operation": "delete"})
    assert delete_result.success
    assert "DELETE FROM provider.p_alt_id_tb" in delete_result.runtime_payload[0]

    upsert_result = agent.run({"action": "generate_sql", "table": table, "rows": rows, "operation": "upsert"})
    assert upsert_result.success
    assert "ON CONFLICT" in upsert_result.runtime_payload[0]


def test_sql_generation_agent_refuses_rollback_without_primary_key(tmp_path):
    from core.database_port import ColumnMetadata, TableMetadata

    agent = SQLGenerationAgent(_storage(tmp_path))
    table = TableMetadata(name="no_pk_tb", schema="provider", columns=[ColumnMetadata("x", "VARCHAR(10)")], primary_key=[])
    result = agent.run({"action": "build_rollback_plan", "table": table, "rows": [{"x": "1"}]})
    assert not result.success


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
