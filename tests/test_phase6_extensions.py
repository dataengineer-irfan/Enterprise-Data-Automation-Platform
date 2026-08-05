"""
tests/test_phase6_extensions.py — Phase 6 extensions test suite.

Verifies:
1. Format-Preserving Encryption (FPE) in core.masking
2. Multi-operation SQL generation (insert, update, upsert, delete)
3. CLI fan-out command parsing and execution
"""
from __future__ import annotations

import json
from pathlib import Path
from core.database_port import ColumnMetadata, TableMetadata
from core.masking import MaskingEngine, MaskingRule, MaskStrategy
from agent.subagents.sql_generation_agent import SQLGenerationAgent
from cli import build_parser


def test_fpe_masking_format_preservation():
    engine = MaskingEngine()
    rule = MaskingRule(column="ssn", strategy=MaskStrategy.FORMAT_PRESERVING, salt="test-salt-123")

    val1 = "123-45-6789"
    masked1 = engine.mask_value(val1, rule)
    assert len(masked1) == len(val1)
    assert masked1[3] == "-" and masked1[6] == "-"
    assert masked1 != val1
    assert masked1.replace("-", "").isdigit()

    # Determinism check: same input + salt -> same output
    masked1_again = engine.mask_value(val1, rule)
    assert masked1 == masked1_again

    # Different salt -> different output
    rule_diff_salt = MaskingRule(column="ssn", strategy=MaskStrategy.FORMAT_PRESERVING, salt="diff-salt-456")
    masked_diff = engine.mask_value(val1, rule_diff_salt)
    assert masked_diff != masked1


def test_sql_generation_multi_operations(tmp_path):
    from agent.shared_storage import SharedStorage
    agent = SQLGenerationAgent(SharedStorage(tmp_path))
    table = TableMetadata(
        name="p_alt_id_tb",
        schema="provider",
        columns=[
            ColumnMetadata(name="p_sys_id", data_type="VARCHAR", is_primary_key=True),
            ColumnMetadata(name="alt_id", data_type="VARCHAR"),
            ColumnMetadata(name="status", data_type="VARCHAR"),
        ],
        primary_key=["p_sys_id"],
    )
    rows = [
        {"p_sys_id": "SYS-001", "alt_id": "ALT-100", "status": "ACTIVE"},
        {"p_sys_id": "SYS-002", "alt_id": "ALT-200", "status": "INACTIVE"},
    ]

    # 1. INSERT
    res_insert = agent.generate_sql(table, rows, operation="insert")
    assert res_insert.success
    script_insert = "\n".join(res_insert.runtime_payload)
    assert "INSERT INTO provider.p_alt_id_tb" in script_insert
    assert "VALUES ('SYS-001'" in script_insert

    # 2. UPDATE
    res_update = agent.generate_sql(table, rows, operation="update")
    assert res_update.success
    script_update = "\n".join(res_update.runtime_payload)
    assert "UPDATE provider.p_alt_id_tb SET" in script_update
    assert "WHERE p_sys_id = 'SYS-001'" in script_update

    # 3. UPSERT
    res_upsert = agent.generate_sql(table, rows, operation="upsert")
    assert res_upsert.success
    script_upsert = "\n".join(res_upsert.runtime_payload)
    assert "ON CONFLICT (p_sys_id) DO UPDATE SET" in script_upsert

    # 4. DELETE
    res_delete = agent.generate_sql(table, rows, operation="delete")
    assert res_delete.success
    script_delete = "\n".join(res_delete.runtime_payload)
    assert "DELETE FROM provider.p_alt_id_tb WHERE p_sys_id = 'SYS-001'" in script_delete


def test_cli_fanout_parser():
    parser = build_parser()
    args = parser.parse_args(["fan-out", "--tables", "p_dtl_tb", "p_alt_id_tb", "--rows", "15"])
    assert args.command == "fan-out"
    assert args.tables == ["p_dtl_tb", "p_alt_id_tb"]
    assert args.rows == 15
