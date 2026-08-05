"""
tests/test_phase7_connectors_scaleout.py — Phase 7 cloud connectors & scale-out test suite.

Verifies:
1. All Phase 7 DatabasePort adapters (Snowflake, BigQuery, Redshift, MySQL, SQLite)
2. Adapter Factory (get_adapter)
3. Source-only connection write safeguards across all adapters
"""
from __future__ import annotations

import pytest
from core.database_port import TableMetadata, ColumnMetadata
from adapters import (
    get_adapter,
    SnowflakeAdapter,
    BigQueryAdapter,
    RedshiftAdapter,
    MySQLAdapter,
    SQLiteAdapter,
)


@pytest.mark.parametrize(
    "engine_name,adapter_cls",
    [
        ("snowflake", SnowflakeAdapter),
        ("bigquery", BigQueryAdapter),
        ("redshift", RedshiftAdapter),
        ("mysql", MySQLAdapter),
        ("sqlite", SQLiteAdapter),
    ],
)
def test_phase7_adapters_contract(engine_name, adapter_cls):
    adapter = get_adapter(engine_name, {"database": ":memory:"}, schema="provider")
    assert isinstance(adapter, adapter_cls)
    assert adapter.connect() is None
    assert adapter.test_connection() is True

    if engine_name == "sqlite":
        adapter.execute("CREATE TABLE p_test_tb (id VARCHAR PRIMARY KEY, val VARCHAR)")

    # Introspection
    tables = adapter.introspect_schema("provider")
    assert isinstance(tables, list)
    assert len(tables) >= 1

    # DDL generation
    tbl = TableMetadata(
        name="p_test_tb",
        schema="provider",
        columns=[
            ColumnMetadata(name="id", data_type="VARCHAR", is_primary_key=True),
            ColumnMetadata(name="val", data_type="VARCHAR"),
        ],
        primary_key=["id"],
    )
    ddl = adapter.generate_ddl(tbl)
    assert "CREATE TABLE" in ddl
    assert "p_test_tb" in ddl or "P_TEST_TB" in ddl

    # Dialect validation
    warnings = adapter.validate_dialect(ddl)
    assert isinstance(warnings, list)

    # Execution & Source-Only Guard
    res_write = adapter.execute("INSERT INTO p_test_tb VALUES ('1', 'test')")
    assert res_write.success

    # Source-only write restriction check
    source_only_adapter = get_adapter(engine_name, {"database": ":memory:"}, schema="provider", is_source_only=True)
    res_refused = source_only_adapter.execute("INSERT INTO p_test_tb VALUES ('1', 'test')")
    assert res_refused.success is False
    assert "Refused" in res_refused.error

    adapter.disconnect()
