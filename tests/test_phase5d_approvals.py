"""
tests/test_phase5d_approvals.py — Phase 5d: verifies the actual claim the
Approval Dashboard screen exists to satisfy: a plan created (and previewed)
by one user session is discoverable and actionable by a DIFFERENT user's
session via GET /api/jobs -> GET /api/agent/{id} -> POST confirm -> POST
execute, without either session needing to be the one that created it.

Before this screen existed, Agent Console only ever showed the single plan
your own browser session had just created, in local component state — a
plan sitting in awaiting_confirmation from any other source was invisible
and unreachable through the UI. This test is the backend-side proof that
claim is actually true, independent of the UI.

Run: python -m pytest tests/test_phase5d_approvals.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_plan_created_by_one_session_is_confirmable_by_another(tmp_path):
    manager = ManagerAgent(
        llm=FakeLLMProvider({"intent": "validate", "table": "p_alt_id_tb", "reasoning": "quality check", "clarifying_question": None}),
        config_dir=CONFIG_DIR, ddl_dir=DDL_DIR,
        plan_memory=PlanMemory(tmp_path / "plans"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        shared_storage=SharedStorage(tmp_path / "shared"),
    )
    dependencies.set_manager(manager)
    client = TestClient(app)

    try:
        operator_token = client.post("/api/auth/login", json={"username": "operator", "password": "operator-dev-pw"}).json()["token"]
        admin_token = client.post("/api/auth/login", json={"username": "admin", "password": "admin-dev-pw"}).json()["token"]

        # Session A (operator): creates and previews a plan. This is
        # everything Agent Console alone could ever do for this plan
        # before the Approval Dashboard existed.
        plan = client.post(
            "/api/agent/plan", json={"nl_request": "validate p_alt_id_tb"},
            headers={"Authorization": f"Bearer {operator_token}"},
        ).json()
        assert plan["intent"] == "validate"
        plan_id = plan["plan_id"]

        preview_resp = client.post(
            f"/api/agent/{plan_id}/preview", json={"use_sample_data": True},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert preview_resp.status_code == 200

        # Session B (admin, a DIFFERENT login): never saw session A's plan
        # get created. Discovers it purely through /api/jobs, exactly what
        # ApprovalDashboard.refresh() does.
        jobs = client.get("/api/jobs", headers={"Authorization": f"Bearer {admin_token}"}).json()["jobs"]
        pending = [j for j in jobs if j["status"] == "awaiting_confirmation"]
        assert any(j["plan_id"] == plan_id for j in pending), "operator's plan must be visible to a different admin session"

        # Fetches full detail purely by plan_id, exactly what
        # ApprovalDashboard.openDetail() does.
        detail = client.get(f"/api/agent/{plan_id}", headers={"Authorization": f"Bearer {admin_token}"}).json()
        assert detail["preview"] is not None

        # Confirms and executes — a plan operator created, admin approved,
        # neither having the other's browser session or local UI state.
        confirm_resp = client.post(
            f"/api/agent/{plan_id}/confirm", json={"confirmed": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "confirmed"

        execute_resp = client.post(f"/api/agent/{plan_id}/execute", headers={"Authorization": f"Bearer {admin_token}"})
        assert execute_resp.status_code == 200
        assert execute_resp.json()["action"] == "validate"

        # And it's gone from the pending list now that it's completed.
        jobs_after = client.get("/api/jobs", headers={"Authorization": f"Bearer {admin_token}"}).json()["jobs"]
        pending_after = [j for j in jobs_after if j["status"] == "awaiting_confirmation"]
        assert not any(j["plan_id"] == plan_id for j in pending_after)
    finally:
        dependencies.reset_for_tests()


def test_operator_cannot_approve_even_their_own_plan(tmp_path):
    """The ADMIN-only gate on confirm/execute (ADR-0008) applies just as
    much through the Approvals path as through Agent Console — an operator
    can create and preview a plan but cannot be the one to approve it,
    even their own."""
    manager = ManagerAgent(
        llm=FakeLLMProvider({"intent": "validate", "table": "p_alt_id_tb", "reasoning": "x", "clarifying_question": None}),
        config_dir=CONFIG_DIR, ddl_dir=DDL_DIR,
        plan_memory=PlanMemory(tmp_path / "plans"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        shared_storage=SharedStorage(tmp_path / "shared"),
    )
    dependencies.set_manager(manager)
    client = TestClient(app)

    try:
        operator_token = client.post("/api/auth/login", json={"username": "operator", "password": "operator-dev-pw"}).json()["token"]
        plan_id = client.post(
            "/api/agent/plan", json={"nl_request": "validate p_alt_id_tb"},
            headers={"Authorization": f"Bearer {operator_token}"},
        ).json()["plan_id"]
        client.post(f"/api/agent/{plan_id}/preview", json={"use_sample_data": True}, headers={"Authorization": f"Bearer {operator_token}"})

        resp = client.post(
            f"/api/agent/{plan_id}/confirm", json={"confirmed": True},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert resp.status_code == 403
    finally:
        dependencies.reset_for_tests()


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
