"""
adapters/__init__.py — Adapter registry and factory for DatabasePort implementations.
"""
from __future__ import annotations

from typing import Type

from core.database_port import DatabasePort
from adapters.postgres_adapter import PostgresAdapter
from adapters.oracle_ddl_adapter import OracleDDLAdapter
from adapters.snowflake_adapter import SnowflakeAdapter
from adapters.bigquery_adapter import BigQueryAdapter
from adapters.redshift_adapter import RedshiftAdapter
from adapters.mysql_adapter import MySQLAdapter
from adapters.sqlite_adapter import SQLiteAdapter

ADAPTER_REGISTRY: dict[str, Type[DatabasePort]] = {
    "postgres": PostgresAdapter,
    "postgresql": PostgresAdapter,
    "oracle": OracleDDLAdapter,
    "snowflake": SnowflakeAdapter,
    "bigquery": BigQueryAdapter,
    "redshift": RedshiftAdapter,
    "mysql": MySQLAdapter,
    "mariadb": MySQLAdapter,
    "sqlite": SQLiteAdapter,
}


def get_adapter(engine_name: str, db_config: dict, schema: str = "provider", is_source_only: bool = False) -> DatabasePort:
    engine_key = engine_name.lower()
    adapter_cls = ADAPTER_REGISTRY.get(engine_key)
    if not adapter_cls:
        available = ", ".join(sorted(set(ADAPTER_REGISTRY.keys())))
        raise ValueError(f"Unsupported database engine {engine_name!r}. Available adapters: {available}")
    return adapter_cls(db_config, schema=schema, is_source_only=is_source_only)
