"""
tests/test_phase1_foundation.py — Phase 1 smoke tests.

Runs without a live database (Oracle-adapter path + audit/glossary/graph
are all file-based). The Postgres write-refusal test also runs without a
live DB, since it must fail before ever opening a connection.

Run: python -m pytest tests/test_phase1_foundation.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.oracle_ddl_adapter import OracleDDLAdapter
from adapters.postgres_adapter import PostgresAdapter
from core.audit import AuditLog
from core.glossary import load_default_glossary
from core.schema_graph import load_default_schema_graph

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"


def test_schema_graph_loads_and_orders_parents_first():
    graph = load_default_schema_graph(CONFIG_DIR)
    assert graph.core_table == "P_DTL_TB"
    order = graph.topological_insert_order()
    assert order.index("P_DTL_TB") < order.index("P_TY_TB")
    assert "P_LIC_CERT_TB" in graph.tables_required_for_active()


def test_glossary_resolves_known_term():
    glossary = load_default_glossary(CONFIG_DIR)
    term = glossary.resolve_glossary_term("NPI")
    assert term is not None
    assert term.physical_table == "PROVIDER"
    assert glossary.is_valid_code("Provider Status", "ACT")
    assert not glossary.is_valid_code("Provider Status", "NOT_A_CODE")


def test_oracle_adapter_introspects_real_ddl_export():
    graph = load_default_schema_graph(CONFIG_DIR)
    adapter = OracleDDLAdapter(DDL_DIR, graph)
    adapter.connect()
    assert adapter.test_connection()

    tables = adapter.introspect_schema("provider")
    assert len(tables) > 100

    p_dtl_tb = next(t for t in tables if t.name == "p_dtl_tb")
    assert p_dtl_tb.primary_key == ["p_sys_id"]
    assert len(p_dtl_tb.columns) > 50
    assert any(c.is_primary_key for c in p_dtl_tb.columns)


def test_oracle_adapter_refuses_writes_by_design():
    graph = load_default_schema_graph(CONFIG_DIR)
    adapter = OracleDDLAdapter(DDL_DIR, graph)
    result = adapter.execute("DELETE FROM p_dtl_tb")
    assert result.success is False


def test_postgres_adapter_refuses_writes_when_source_only():
    adapter = PostgresAdapter(
        {"host": "localhost", "port": 5432, "dbname": "x", "user": "x", "password": "x"},
        schema="provider",
        is_source_only=True,
    )
    result = adapter.execute("DELETE FROM provider.p_dtl_tb")
    assert result.success is False
    assert "source-only" in result.error


def test_audit_log_redacts_secrets(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    rec = log.record(
        actor="dev",
        action="execute_sql",
        resource="provider.p_dtl_tb",
        result="failure",
        detail={"sql": "DELETE FROM provider.p_dtl_tb", "db_password": "sup3rsecret"},
    )
    assert "sup3rsecret" not in rec.to_json()
    stored = log.read_all()
    assert len(stored) == 1
    assert stored[0]["detail"]["db_password"] == "***REDACTED***"


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
