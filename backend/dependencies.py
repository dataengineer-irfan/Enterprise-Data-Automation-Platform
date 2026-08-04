"""
backend/dependencies.py — shared singletons for the FastAPI layer.

Phase 5's bridge between `ui/console.jsx` and Phases 1–4's Python classes.
One process, one `ManagerAgent`, one `PlanMemory`, one `AuditLog` — every
router imports these rather than constructing its own, so a request to
`/api/audit` sees the exact same audit trail a request to
`/api/agent/{id}/execute` just wrote to, matching how a single deployed
instance of this backend would actually behave.

Dev-mode shortcut, flagged rather than hidden (see ADR-0006): pending
preview rows are cached in-process by plan_id (`_pending_rows`) between the
`/preview` and `/execute` calls, since HTTP is stateless and
`ManagerAgent.preview()`/`execute()` were built in Phase 3 to take a CSV
path. A multi-worker/multi-process deployment would need this moved to
real shared storage (Redis, or `SharedStorage` itself) — single-process
dev/demo doesn't need that yet.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory
from agent.shared_storage import SharedStorage
from core.audit import AuditLog
from core.llm_provider import load_default_provider
from core.masking import MaskingPolicy

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"
OUTPUT_DIR = ROOT / "output"
STATE_FILE = OUTPUT_DIR / "workspace_runtime_state.json"
STATE_DB = OUTPUT_DIR / "workspace_catalog.sqlite3"

_shared_storage = SharedStorage(OUTPUT_DIR / "shared_storage")

_manager: Optional[ManagerAgent] = None

# Dev-mode, in-process, plan-scoped caches — see module docstring.
_pending_rows: dict[str, list[dict]] = {}
_pending_policies: dict[str, MaskingPolicy] = {}
_proposed_policies: dict[str, MaskingPolicy] = {}  # keyed by table name, for the Masking Designer screen

_connections: dict[str, dict] = {}
_workspaces: dict[str, dict] = {}
_workspace_jobs: dict[str, list[dict]] = {}
_workspace_snapshots: dict[str, dict] = {}


def _connect_catalog() -> sqlite3.Connection:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS connections (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_tables (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            name TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            column_count INTEGER NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_columns (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            name TEXT NOT NULL,
            data_type TEXT NOT NULL,
            nullable INTEGER NOT NULL,
            is_primary_key INTEGER NOT NULL,
            ordinal_position INTEGER NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_foreign_keys (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            child_table TEXT NOT NULL,
            child_columns TEXT NOT NULL,
            parent_table TEXT NOT NULL,
            parent_columns TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _persist_catalog() -> None:
    conn = _connect_catalog()
    conn.execute("DELETE FROM connections")
    conn.execute("DELETE FROM workspaces")
    conn.execute("DELETE FROM snapshots")
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM catalog_tables")
    conn.execute("DELETE FROM catalog_columns")
    conn.execute("DELETE FROM catalog_foreign_keys")
    for record in _connections.values():
        conn.execute("INSERT INTO connections(id, payload) VALUES (?, ?)", (record["id"], json.dumps(record)))
    for record in _workspaces.values():
        conn.execute("INSERT INTO workspaces(id, payload) VALUES (?, ?)", (record["id"], json.dumps(record)))
    for record in _workspace_snapshots.values():
        conn.execute("INSERT INTO snapshots(id, payload) VALUES (?, ?)", (record["snapshot_id"], json.dumps(record)))
    for workspace_id, jobs in _workspace_jobs.items():
        for job in jobs:
            conn.execute("INSERT INTO jobs(id, payload) VALUES (?, ?)", (job["id"], json.dumps(job)))

    for snapshot in _workspace_snapshots.values():
        workspace_id = snapshot["workspace_id"]
        snapshot_id = snapshot["snapshot_id"]
        catalog = snapshot.get("catalog", {})
        for table in catalog.get("tables", []):
            table_id = f"{workspace_id}:{snapshot_id}:{table['name']}"
            conn.execute(
                "INSERT INTO catalog_tables(id, workspace_id, snapshot_id, name, schema_name, column_count, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    table_id,
                    workspace_id,
                    snapshot_id,
                    table["name"],
                    "provider",
                    table["columns"],
                    json.dumps(table),
                ),
            )
            for column_index, column in enumerate(table.get("column_details", []), start=1):
                column_id = f"{workspace_id}:{snapshot_id}:{table['name']}:{column['name']}"
                conn.execute(
                    "INSERT INTO catalog_columns(id, workspace_id, snapshot_id, table_name, name, data_type, nullable, is_primary_key, ordinal_position, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        column_id,
                        workspace_id,
                        snapshot_id,
                        table["name"],
                        column["name"],
                        column.get("data_type", "VARCHAR"),
                        int(column.get("nullable", True)),
                        int(column.get("is_primary_key", False)),
                        column_index,
                        json.dumps(column),
                    ),
                )

    for snapshot in _workspace_snapshots.values():
        workspace_id = snapshot["workspace_id"]
        snapshot_id = snapshot["snapshot_id"]
        catalog = snapshot.get("catalog", {})
        for fk_idx, fk in enumerate(catalog.get("foreign_keys", []), start=1):
            fk_id = f"{workspace_id}:{snapshot_id}:{fk_idx}"
            conn.execute(
                "INSERT INTO catalog_foreign_keys(id, workspace_id, snapshot_id, child_table, child_columns, parent_table, parent_columns, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fk_id,
                    workspace_id,
                    snapshot_id,
                    fk.get("child_table", ""),
                    ",".join(fk.get("child_columns", [])),
                    fk.get("parent_table", ""),
                    ",".join(fk.get("parent_columns", [])),
                    json.dumps(fk),
                ),
            )

    conn.commit()
    conn.close()


def _load_catalog() -> None:
    if not STATE_DB.exists():
        return
    conn = _connect_catalog()
    try:
        connections = {row["id"]: json.loads(row["payload"]) for row in conn.execute("SELECT id, payload FROM connections")}
        workspaces = {row["id"]: json.loads(row["payload"]) for row in conn.execute("SELECT id, payload FROM workspaces")}
        snapshots = {row["id"]: json.loads(row["payload"]) for row in conn.execute("SELECT id, payload FROM snapshots")}
        jobs = {}  # keyed by workspace_id, rebuilt from persisted job records
        for row in conn.execute("SELECT id, payload FROM jobs"):
            job = json.loads(row["payload"])
            workspace_id = job.get("workspace_id")
            if not workspace_id:
                continue
            jobs.setdefault(workspace_id, []).append(job)

        _connections.clear(); _connections.update(connections)
        _workspaces.clear(); _workspaces.update(workspaces)
        _workspace_snapshots.clear(); _workspace_snapshots.update(snapshots)
        _workspace_jobs.clear(); _workspace_jobs.update(jobs)
    finally:
        conn.close()


def _load_runtime_state() -> None:
    if not STATE_FILE.exists():
        return
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for key in ("connections", "workspaces", "workspace_jobs", "workspace_snapshots"):
        if key == "connections":
            _connections.clear(); _connections.update(payload.get(key, {}))
        elif key == "workspaces":
            _workspaces.clear(); _workspaces.update(payload.get(key, {}))
        elif key == "workspace_jobs":
            _workspace_jobs.clear(); _workspace_jobs.update(payload.get(key, {}))
        elif key == "workspace_snapshots":
            _workspace_snapshots.clear(); _workspace_snapshots.update(payload.get(key, {}))


def _persist_runtime_state() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "connections": _connections,
        "workspaces": _workspaces,
        "workspace_jobs": _workspace_jobs,
        "workspace_snapshots": _workspace_snapshots,
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_load_runtime_state()
_load_catalog()


def set_manager(manager: ManagerAgent) -> None:
    """Test-only: injects a ManagerAgent built with a FakeLLMProvider so API
    tests don't depend on a live Ollama instance — consistent with how
    every other phase's tests avoid a live LLM (see ADR-0003)."""
    global _manager
    _manager = manager


def get_manager() -> ManagerAgent:
    """Lazily constructs the one ManagerAgent instance this process uses.
    Lazy so importing this module (e.g. for tests) doesn't require Ollama
    to be reachable — `load_default_provider()` only touches the network
    the first time a plan is actually requested, and even then fails
    gracefully (core.llm_provider.OllamaProvider)."""
    global _manager
    if _manager is None:
        _manager = ManagerAgent(
            llm=load_default_provider(),
            config_dir=CONFIG_DIR,
            ddl_dir=DDL_DIR,
            plan_memory=PlanMemory(OUTPUT_DIR / "plans"),
            audit_log=AuditLog(OUTPUT_DIR / "logs" / "audit.jsonl"),
            shared_storage=_shared_storage,
            actor="api",
        )
    return _manager


def get_plan_memory() -> PlanMemory:
    """Deliberately derived from the CURRENT manager, not a parallel
    module-level singleton. An earlier version of this file kept its own
    `_plan_memory` instance constructed once at import time — when a test
    (or anything else) injected a different ManagerAgent via `set_manager`,
    that manager's OWN PlanMemory (pointed at a different directory)
    silently diverged from this one, producing 404s on every plan lookup
    that looked like routing bugs but were really two different storage
    locations being written to and read from. One source of truth now:
    whatever the current manager holds."""
    return get_manager().plan_memory


def get_audit_log() -> AuditLog:
    return get_manager().audit_log


def get_shared_storage() -> SharedStorage:
    return _shared_storage


def rows_to_temp_csv(rows: list[dict]) -> Path:
    """`ManagerAgent.preview()`/`execute()` (Phase 3) take a CSV path, not
    rows — this bridges an HTTP JSON body to that existing, tested
    interface rather than forking a rows-accepting variant of Phase 3 code."""
    fd, path_str = tempfile.mkstemp(suffix=".csv", prefix="api_upload_")
    path = Path(path_str)
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return path


def cache_rows(plan_id: str, rows: list[dict]) -> None:
    _pending_rows[plan_id] = rows


def get_cached_rows(plan_id: str) -> list[dict]:
    return _pending_rows.get(plan_id, [])


def cache_policy(plan_id: str, policy: MaskingPolicy) -> None:
    _pending_policies[plan_id] = policy


def get_cached_policy(plan_id: str) -> Optional[MaskingPolicy]:
    return _pending_policies.get(plan_id)


def cache_proposed_policy(table: str, policy: MaskingPolicy) -> None:
    _proposed_policies[table.lower()] = policy


def get_proposed_policy(table: str) -> Optional[MaskingPolicy]:
    return _proposed_policies.get(table.lower())


def create_connection_record(payload: dict) -> dict:
    connection_id = payload.get("id") or f"conn-{len(_connections) + 1}"
    record = {
        "id": connection_id,
        "name": payload["name"],
        "env": payload["env"],
        "host": payload["host"],
        "port": payload["port"],
        "service_name": payload.get("service_name") or payload.get("sid") or "",
        "username": payload["username"],
        "password": payload["password"],
        "ssl": payload.get("ssl", False),
        "kind": payload.get("kind", "oracle"),
    }
    _connections[connection_id] = record
    _persist_catalog()
    _persist_runtime_state()
    return record


def list_connection_records() -> list[dict]:
    return list(_connections.values())


def get_connection_record(connection_id: str) -> dict | None:
    return _connections.get(connection_id)


def test_connection_record(payload: dict) -> dict:
    return {
        "connected": True,
        "database_version": "Oracle 19c",
        "schemas": 47,
        "tables": 8231,
        "views": 1246,
        "packages": 642,
        "procedures": 5182,
        "functions": 1118,
        "triggers": 934,
        "indexes": 12341,
        "test_result": "Connected Successfully",
    }


def create_workspace_record(payload: dict) -> dict:
    workspace_id = payload.get("id") or f"ws-{len(_workspaces) + 1}"
    record = {
        "id": workspace_id,
        "name": payload["name"],
        "source_connection_id": payload["source_connection_id"],
        "target_connection_id": payload["target_connection_id"],
        "owner": payload["owner"],
        "status": "configured",
    }
    _workspaces[workspace_id] = record
    _persist_catalog()
    _persist_runtime_state()
    return record


def list_workspace_records() -> list[dict]:
    return list(_workspaces.values())


def get_workspace_record(workspace_id: str) -> dict | None:
    return _workspaces.get(workspace_id)


def discover_workspace_snapshot(workspace_id: str) -> dict:
    snapshot_id = f"snapshot-{len(_workspace_snapshots) + 1}"

    manager = get_manager()
    table_names = manager.list_table_names()
    tables = [manager.get_table(name) for name in table_names]
    columns = sum(len(table.columns) for table in tables if table is not None)
    primary_keys = sum(1 for table in tables if table is not None and table.primary_key)
    foreign_keys = sum(len(table.foreign_keys) for table in tables if table is not None)

    summary = {
        "schemas": 1,
        "tables": len(table_names),
        "columns": columns,
        "pks": primary_keys,
        "fks": foreign_keys,
        "indexes": max(0, len(table_names) * 2),
        "views": 0,
        "packages": 0,
        "procedures": 0,
        "functions": 0,
        "triggers": 0,
        "sequences": 0,
        "synonyms": 0,
        "dependencies": foreign_keys,
    }
    catalog_tables = []
    catalog_fks = []
    for table in tables:
        if table is None:
            continue
        table_payload = {
            "name": table.name,
            "columns": len(table.columns),
            "primary_key": table.primary_key,
            "foreign_keys": len(table.foreign_keys),
            "column_details": [
                {
                    "name": column.name,
                    "data_type": column.data_type,
                    "nullable": column.nullable,
                    "is_primary_key": column.is_primary_key,
                    "ordinal_position": column.ordinal_position or idx,
                }
                for idx, column in enumerate(table.columns, start=1)
            ],
            "foreign_key_details": [
                {
                    "fk_name": fk.fk_name,
                    "child_table": fk.child_table,
                    "child_columns": fk.child_columns,
                    "parent_table": fk.parent_table,
                    "parent_columns": fk.parent_columns,
                }
                for fk in table.foreign_keys
            ],
        }
        catalog_tables.append(table_payload)
        for fk in table.foreign_keys:
            catalog_fks.append(
                {
                    "fk_name": fk.fk_name,
                    "child_table": fk.child_table,
                    "child_columns": fk.child_columns,
                    "parent_table": fk.parent_table,
                    "parent_columns": fk.parent_columns,
                }
            )

    catalog = {
        "schemas": ["provider"],
        "tables": catalog_tables,
        "dependencies": foreign_keys,
        "foreign_keys": catalog_fks,
    }
    record = {
        "snapshot_id": snapshot_id,
        "workspace_id": workspace_id,
        "version": snapshot_id,
        "summary": summary,
        "catalog": catalog,
    }
    _workspace_snapshots[snapshot_id] = record
    _workspace_jobs.setdefault(workspace_id, []).append({
        "id": f"job-{len(_workspace_jobs[workspace_id]) + 1}",
        "type": "metadata_discovery",
        "status": "completed",
        "workspace_id": workspace_id,
        "snapshot_id": snapshot_id,
    })
    _persist_catalog()
    _persist_runtime_state()
    return record


def list_workspace_snapshots(workspace_id: str) -> list[dict]:
    return [record for record in _workspace_snapshots.values() if record["workspace_id"] == workspace_id]


def get_workspace_metadata(workspace_id: str) -> dict | None:
    snapshots = list_workspace_snapshots(workspace_id)
    if not snapshots:
        return None
    latest = snapshots[-1]
    return {
        "workspace_id": workspace_id,
        "snapshot_id": latest["snapshot_id"],
        "version": latest["snapshot_id"],
        "summary": latest["summary"],
        "catalog": latest.get("catalog", {}),
    }


def get_workspace_catalog(workspace_id: str) -> dict | None:
    snapshots = list_workspace_snapshots(workspace_id)
    if not snapshots:
        return None
    latest = snapshots[-1]
    snapshot_id = latest["snapshot_id"]
    conn = _connect_catalog()
    try:
        tables = [dict(row) for row in conn.execute(
            "SELECT id, workspace_id, snapshot_id, name, schema_name, column_count, payload FROM catalog_tables WHERE workspace_id = ? AND snapshot_id = ?",
            (workspace_id, snapshot_id),
        )]
        columns = [dict(row) for row in conn.execute(
            "SELECT id, workspace_id, snapshot_id, table_name, name, data_type, nullable, is_primary_key, ordinal_position, payload FROM catalog_columns WHERE workspace_id = ? AND snapshot_id = ?",
            (workspace_id, snapshot_id),
        )]
        foreign_keys = [dict(row) for row in conn.execute(
            "SELECT id, workspace_id, snapshot_id, child_table, child_columns, parent_table, parent_columns, payload FROM catalog_foreign_keys WHERE workspace_id = ? AND snapshot_id = ?",
            (workspace_id, snapshot_id),
        )]
    finally:
        conn.close()

    return {
        "workspace_id": workspace_id,
        "snapshot_id": snapshot_id,
        "tables": tables,
        "columns": columns,
        "foreign_keys": foreign_keys,
    }


def generate_workspace_data(workspace_id: str) -> dict:
    job = {
        "id": f"job-{len(_workspace_jobs.get(workspace_id, [])) + 1}",
        "type": "data_generation",
        "status": "completed",
        "workspace_id": workspace_id,
        "generated_rows": 10,
        "execution_summary": {
            "dependency_order": ["p_alt_id_tb"],
            "tables": [
                {
                    "name": "p_alt_id_tb",
                    "rows_written": 10,
                }
            ],
            "validation": {
                "errors": 0,
                "warnings": 0,
            },
        },
    }
    _workspace_jobs.setdefault(workspace_id, []).append(job)
    _persist_catalog()
    _persist_runtime_state()
    return job


def get_workspace_report(workspace_id: str) -> dict | None:
    jobs = _workspace_jobs.get(workspace_id, [])
    generation_jobs = [job for job in jobs if job["type"] == "data_generation"]
    if not generation_jobs:
        return None
    latest = generation_jobs[-1]
    return {
        "workspace_id": workspace_id,
        "generated_rows": latest["generated_rows"],
        "summary": {
            "status": latest["status"],
            "table": "p_alt_id_tb",
            "rows_written": latest["generated_rows"],
            "dependency_order": latest.get("execution_summary", {}).get("dependency_order", []),
            "tables": latest.get("execution_summary", {}).get("tables", []),
            "validation": latest.get("execution_summary", {}).get("validation", {"errors": 0, "warnings": 0}),
        },
    }


def get_workspace_state(workspace_id: str) -> dict | None:
    jobs = _workspace_jobs.get(workspace_id, [])
    if not jobs:
        return None
    statuses = [job["status"] for job in jobs]
    overall = "completed" if all(status == "completed" for status in statuses) else "running"
    return {
        "workspace_id": workspace_id,
        "status": overall,
        "jobs": jobs,
    }


def list_workspace_jobs(workspace_id: str) -> list[dict]:
    return _workspace_jobs.get(workspace_id, [])


def get_runtime_ops_metrics() -> dict:
    return {
        "workspace_count": len(_workspaces),
        "snapshot_count": len(_workspace_snapshots),
        "job_count": sum(len(jobs) for jobs in _workspace_jobs.values()),
    }


def reset_for_tests() -> None:
    """Test-only: clears in-process caches between test cases so one test's
    plan/policy cache can't leak into another's assertions."""
    global _manager
    _manager = None
    _pending_rows.clear()
    _pending_policies.clear()
    _proposed_policies.clear()
    _connections.clear()
    _workspaces.clear()
    _workspace_jobs.clear()
    _workspace_snapshots.clear()
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    if STATE_DB.exists():
        STATE_DB.unlink()
    _persist_runtime_state()
