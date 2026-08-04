from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory
from agent.shared_storage import SharedStorage
from backend import dependencies
from backend.app import app
from core.audit import AuditLog
from core.llm_provider import LLMProvider, LLMResponse

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"


class FakeLLMProvider(LLMProvider):
    provider_name = "fake"
    model = "fake-model"

    def __init__(self, canned: dict) -> None:
        self._canned = canned

    def complete(self, system_prompt, user_prompt, temperature=0.0) -> LLMResponse:
        return LLMResponse(text=json.dumps(self._canned))


@pytest.fixture()
def client(tmp_path):
    dependencies.reset_for_tests()
    manager = ManagerAgent(
        llm=FakeLLMProvider({"intent": "mask", "table": "p_alt_id_tb", "reasoning": "de-identify", "clarifying_question": None}),
        config_dir=CONFIG_DIR,
        ddl_dir=DDL_DIR,
        plan_memory=PlanMemory(tmp_path / "plans"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        shared_storage=SharedStorage(tmp_path / "shared"),
    )
    dependencies.set_manager(manager)
    yield TestClient(app)
    dependencies.reset_for_tests()


def _login(client, username: str, password: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_headers(client):
    return _login(client, "admin", "admin-dev-pw")


def test_connection_test_and_save_roundtrip(client, admin_headers):
    test_resp = client.post(
        "/api/connections/test",
        json={
            "name": "oracle-source-dev",
            "env": "DEV",
            "host": "oracle01.local",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "username": "system",
            "password": "secret",
            "ssl": False,
            "kind": "oracle",
        },
        headers=admin_headers,
    )
    assert test_resp.status_code == 200
    body = test_resp.json()
    assert body["connected"] is True
    assert body["database_version"]

    save_resp = client.post(
        "/api/connections",
        json={
            "name": "oracle-source-dev",
            "env": "DEV",
            "host": "oracle01.local",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "username": "system",
            "password": "secret",
            "ssl": False,
            "kind": "oracle",
        },
        headers=admin_headers,
    )
    assert save_resp.status_code == 200
    created = save_resp.json()
    assert created["name"] == "oracle-source-dev"
    assert created["id"]

    detail_resp = client.get(f"/api/connections/{created['id']}", headers=admin_headers)
    assert detail_resp.status_code == 200
    detail_payload = detail_resp.json()
    assert detail_payload["id"] == created["id"]
    assert detail_payload["name"] == "oracle-source-dev"


def test_workspace_discovery_snapshot_and_job_flow(client, admin_headers):
    source = client.post(
        "/api/connections",
        json={
            "name": "source-oracle-dev",
            "env": "DEV",
            "host": "oracle01.local",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "username": "system",
            "password": "secret",
            "ssl": False,
            "kind": "oracle",
        },
        headers=admin_headers,
    ).json()
    target = client.post(
        "/api/connections",
        json={
            "name": "target-oracle-qa",
            "env": "QA",
            "host": "oracle02.local",
            "port": 1521,
            "service_name": "QA1",
            "username": "system",
            "password": "secret",
            "ssl": False,
            "kind": "oracle",
        },
        headers=admin_headers,
    ).json()

    workspace = client.post(
        "/api/workspaces",
        json={
            "name": "Healthcare Synthetic Generation",
            "source_connection_id": source["id"],
            "target_connection_id": target["id"],
            "owner": "robin",
        },
        headers=admin_headers,
    )
    assert workspace.status_code == 200
    ws = workspace.json()
    assert ws["name"] == "Healthcare Synthetic Generation"

    workspace_detail = client.get(f"/api/workspaces/{ws['id']}", headers=admin_headers)
    assert workspace_detail.status_code == 200
    workspace_detail_payload = workspace_detail.json()
    assert workspace_detail_payload["id"] == ws["id"]
    assert workspace_detail_payload["name"] == "Healthcare Synthetic Generation"

    discovery = client.post(
        f"/api/workspaces/{ws['id']}/discover",
        headers=admin_headers,
    )
    assert discovery.status_code == 200
    detail = discovery.json()
    assert detail["status"] == "completed"
    assert detail["snapshot_id"]

    snapshots = client.get(f"/api/workspaces/{ws['id']}/snapshots", headers=admin_headers)
    assert snapshots.status_code == 200
    snapshots_payload = snapshots.json()
    assert snapshots_payload["snapshots"]
    assert any(s["snapshot_id"] == detail["snapshot_id"] for s in snapshots_payload["snapshots"])

    metadata = client.get(f"/api/workspaces/{ws['id']}/metadata", headers=admin_headers)
    assert metadata.status_code == 200
    metadata_payload = metadata.json()
    assert metadata_payload["workspace_id"] == ws["id"]
    assert metadata_payload["snapshot_id"] == detail["snapshot_id"]
    assert metadata_payload["version"]
    assert metadata_payload["summary"]
    assert metadata_payload["catalog"]
    assert metadata_payload["catalog"]["schemas"]
    assert metadata_payload["catalog"]["tables"]
    assert metadata_payload["catalog"]["tables"][0]["name"]
    assert metadata_payload["catalog"]["dependencies"] >= 0

    catalog = client.get(f"/api/workspaces/{ws['id']}/catalog", headers=admin_headers)
    assert catalog.status_code == 200
    catalog_payload = catalog.json()
    assert catalog_payload["workspace_id"] == ws["id"]
    assert catalog_payload["snapshot_id"] == detail["snapshot_id"]
    assert catalog_payload["tables"]
    assert catalog_payload["columns"]
    assert catalog_payload["foreign_keys"]

    generate = client.post(
        f"/api/workspaces/{ws['id']}/generate",
        json={"table": "p_alt_id_tb", "row_count": 10, "masking": True},
        headers=admin_headers,
    )
    assert generate.status_code == 200
    generation_payload = generate.json()
    assert generation_payload["status"] == "completed"
    assert generation_payload["generated_rows"] == 10

    report = client.get(f"/api/workspaces/{ws['id']}/report", headers=admin_headers)
    assert report.status_code == 200
    report_payload = report.json()
    assert report_payload["workspace_id"] == ws["id"]
    assert report_payload["generated_rows"] == 10
    assert report_payload["summary"]
    assert report_payload["summary"]["dependency_order"]
    assert report_payload["summary"]["tables"]
    assert report_payload["summary"]["validation"]
    assert report_payload["summary"]["validation"]["errors"] == 0

    jobs = client.get(f"/api/workspaces/{ws['id']}/jobs", headers=admin_headers)
    assert jobs.status_code == 200
    payload = jobs.json()
    assert payload["jobs"]
    assert any(j["type"] == "metadata_discovery" and j["status"] == "completed" for j in payload["jobs"])
    assert any(j["type"] == "data_generation" and j["status"] == "completed" for j in payload["jobs"])

    state = client.get(f"/api/workspaces/{ws['id']}/state", headers=admin_headers)
    assert state.status_code == 200
    state_payload = state.json()
    assert state_payload["workspace_id"] == ws["id"]
    assert state_payload["status"] == "completed"
    assert state_payload["jobs"]


def test_workspace_catalog_can_restore_snapshot_metadata_from_sqlite(client, admin_headers):
    source = client.post(
        "/api/connections",
        json={
            "name": "source-oracle-dev",
            "env": "DEV",
            "host": "oracle01.local",
            "port": 1521,
            "service_name": "ORCLPDB1",
            "username": "system",
            "password": "secret",
            "ssl": False,
            "kind": "oracle",
        },
        headers=admin_headers,
    ).json()
    target = client.post(
        "/api/connections",
        json={
            "name": "target-oracle-qa",
            "env": "QA",
            "host": "oracle02.local",
            "port": 1521,
            "service_name": "QA1",
            "username": "system",
            "password": "secret",
            "ssl": False,
            "kind": "oracle",
        },
        headers=admin_headers,
    ).json()

    workspace = client.post(
        "/api/workspaces",
        json={
            "name": "Catalog Recovery Workspace",
            "source_connection_id": source["id"],
            "target_connection_id": target["id"],
            "owner": "robin",
        },
        headers=admin_headers,
    ).json()

    discover = client.post(f"/api/workspaces/{workspace['id']}/discover", headers=admin_headers)
    assert discover.status_code == 200
    snapshot_id = discover.json()["snapshot_id"]

    assert dependencies.STATE_DB.exists()

    conn = dependencies._connect_catalog()
    table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    assert "catalog_tables" in table_names
    assert "catalog_columns" in table_names
    assert "catalog_foreign_keys" in table_names
    conn.close()

    _workspace_snapshots = dict(dependencies._workspace_snapshots)
    _workspace_jobs = dict(dependencies._workspace_jobs)
    _workspaces = dict(dependencies._workspaces)
    _connections = dict(dependencies._connections)

    dependencies._workspace_snapshots.clear()
    dependencies._workspace_jobs.clear()
    dependencies._workspaces.clear()
    dependencies._connections.clear()

    dependencies._load_catalog()

    assert dependencies._workspace_snapshots[snapshot_id]["workspace_id"] == workspace["id"]
    assert dependencies._workspace_snapshots[snapshot_id]["version"] == snapshot_id
    assert dependencies._workspaces[workspace["id"]]["name"] == "Catalog Recovery Workspace"
    assert dependencies._connections[source["id"]]["name"] == "source-oracle-dev"

    dependencies._workspace_snapshots.clear(); dependencies._workspace_snapshots.update(_workspace_snapshots)
    dependencies._workspace_jobs.clear(); dependencies._workspace_jobs.update(_workspace_jobs)
    dependencies._workspaces.clear(); dependencies._workspaces.update(_workspaces)
    dependencies._connections.clear(); dependencies._connections.update(_connections)
