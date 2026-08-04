"""
tests/test_phase5e_ops_dashboard.py — verifies the lightweight operations
health endpoint and its basic metrics payload.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from backend.app import app


def test_health_endpoint_reports_ops_metrics():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "ops" in body
    assert body["ops"]["auth_provider"] in {"local_dev", "keycloak"}
    assert body["ops"]["pending_approvals"] >= 0
    assert body["ops"]["table_count"] >= 1
    assert body["ops"]["workspace_count"] >= 0
    assert body["ops"]["snapshot_count"] >= 0
    assert body["ops"]["job_count"] >= 0
