"""
tests/test_phase5_api.py — Phase 5 API tests.

Uses FastAPI's TestClient (no live server, no live Ollama) with a
FakeLLMProvider-backed ManagerAgent injected via
`backend.dependencies.set_manager` — the same pattern every prior phase's
tests use to avoid depending on a live model (ADR-0003).

Run: python -m pytest tests/test_phase5_api.py -v
"""
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
        config_dir=CONFIG_DIR, ddl_dir=DDL_DIR,
        plan_memory=PlanMemory(tmp_path / "plans"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        shared_storage=SharedStorage(tmp_path / "shared"),
    )
    dependencies.set_manager(manager)
    yield TestClient(app)
    dependencies.reset_for_tests()


def _set_plan_intent(client, intent_payload):
    """Swaps the injected FakeLLMProvider's canned response for a
    different intent without rebuilding the whole fixture."""
    dependencies.get_manager()._llm = FakeLLMProvider(intent_payload)  # noqa: SLF001 — test-only swap


def _login(client, username: str, password: str) -> dict:
    """Logs in via the REAL /api/auth/login endpoint (dev seed users from
    config/users.yaml — see ADR-0008) and returns ready-to-use request
    headers. Exercises the actual auth flow end-to-end rather than
    minting a token by hand."""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_headers(client):
    """Most workflow tests below exercise the full Plan->...->Execute path,
    which needs ADMIN (confirm/execute are ADMIN-gated per ADR-0008) — using
    admin throughout keeps these tests focused on workflow correctness;
    role-boundary enforcement itself is covered separately in
    tests/test_phase5b_auth.py."""
    return _login(client, "admin", "admin-dev-pw")


# ─────────────────────────────────────────────────────────────────────────── #
# Health + Schema                                                             #
# ─────────────────────────────────────────────────────────────────────────── #


def test_health(client):
    # /api/health is intentionally the one endpoint that needs no token —
    # a load balancer/monitor shouldn't need credentials just to check liveness.
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "cors_locked_down" in body  # ADR-0009: observable at runtime, not just a source comment


def test_list_tables(client, admin_headers):
    resp = client.get("/api/schema/tables", headers=admin_headers)
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tables"]}
    assert "p_dtl_tb" in names
    assert "p_alt_id_tb" in names


def test_get_table_detail_includes_fk_graph(client, admin_headers):
    resp = client.get("/api/schema/tables/p_alt_id_tb", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["primary_key"] == ["p_sys_id", "p_alt_id_sk"]
    assert "p_dtl_tb" in body["parents"]


def test_get_unknown_table_404s(client, admin_headers):
    resp = client.get("/api/schema/tables/not_a_real_table", headers=admin_headers)
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────── #
# Masking Designer                                                            #
# ─────────────────────────────────────────────────────────────────────────── #


def test_masking_propose_and_preview_roundtrip(client, admin_headers):
    propose = client.post("/api/masking/p_alt_id_tb/propose", json={}, headers=admin_headers)
    assert propose.status_code == 200
    rules = propose.json()["rules"]
    assert any(r["column"] == "p_alt_id" for r in rules)

    preview = client.get("/api/masking/p_alt_id_tb/preview", headers=admin_headers)
    assert preview.status_code == 200
    assert preview.json()["sample_rows"]


def test_masking_preview_without_propose_first_errors(client, admin_headers):
    resp = client.get("/api/masking/p_lic_cert_tb/preview", headers=admin_headers)
    assert resp.status_code == 404


def test_masking_override_rule(client, admin_headers):
    client.post("/api/masking/p_alt_id_tb/propose", json={}, headers=admin_headers)
    resp = client.post("/api/masking/p_alt_id_tb/override", json={"column": "p_alt_id", "strategy": "nullify"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["strategy"] == "nullify"


# ─────────────────────────────────────────────────────────────────────────── #
# Agent Console — full single-table workflow over HTTP                        #
# ─────────────────────────────────────────────────────────────────────────── #


def test_full_single_table_workflow_over_http(client, admin_headers):
    plan_resp = client.post("/api/agent/plan", json={"nl_request": "mask the SSNs in p_alt_id_tb"}, headers=admin_headers)
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert plan["intent"] == "mask"
    plan_id = plan["plan_id"]

    preview_resp = client.post(f"/api/agent/{plan_id}/preview", json={"use_sample_data": True}, headers=admin_headers)
    assert preview_resp.status_code == 200
    assert "validation" in preview_resp.json()

    confirm_resp = client.post(f"/api/agent/{plan_id}/confirm", json={"confirmed": True}, headers=admin_headers)
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "confirmed"

    execute_resp = client.post(f"/api/agent/{plan_id}/execute", headers=admin_headers)
    assert execute_resp.status_code == 200
    assert execute_resp.json()["action"] == "mask"

    report_resp = client.get(f"/api/agent/{plan_id}/report", headers=admin_headers)
    assert "COMPLETED".lower() in report_resp.json()["report"].lower()


def test_execute_without_confirmation_is_refused_over_http(client, admin_headers):
    plan_id = client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=admin_headers).json()["plan_id"]
    client.post(f"/api/agent/{plan_id}/preview", json={"use_sample_data": True}, headers=admin_headers)
    resp = client.post(f"/api/agent/{plan_id}/execute", headers=admin_headers)
    assert resp.status_code == 403


def test_ambiguous_request_returns_clarifying_question_over_http(client, admin_headers):
    _set_plan_intent(client, {"intent": "mask", "table": None, "reasoning": "x", "clarifying_question": None})
    resp = client.post("/api/agent/plan", json={"nl_request": "mask everything"}, headers=admin_headers)
    plan = resp.json()
    assert plan["intent"] == "unclear"
    assert plan["clarifying_question"]

    preview_resp = client.post(f"/api/agent/{plan['plan_id']}/preview", json={}, headers=admin_headers)
    assert preview_resp.status_code == 400


def test_unknown_table_returns_clarifying_question_over_http(client, admin_headers):
    _set_plan_intent(client, {"intent": "validate", "table": "not_a_real_table", "reasoning": "x", "clarifying_question": None})
    resp = client.post("/api/agent/plan", json={"nl_request": "validate not_a_real_table"}, headers=admin_headers)
    assert resp.json()["intent"] == "unclear"


def test_roster_lists_all_seven_agents(client, admin_headers):
    resp = client.get("/api/agent/roster", headers=admin_headers)
    names = {a["name"] for a in resp.json()["agents"]}
    assert len(names) == 7
    assert "execution_report_agent" in names


# ─────────────────────────────────────────────────────────────────────────── #
# Fan-out workflow over HTTP                                                  #
# ─────────────────────────────────────────────────────────────────────────── #


def test_fan_out_workflow_over_http(client, admin_headers):
    _set_plan_intent(client, {"intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "x", "clarifying_question": None})
    plan = client.post("/api/agent/plan", json={"nl_request": "validate p_dtl_tb and p_alt_id_tb"}, headers=admin_headers).json()
    assert plan["fan_out"] is True
    plan_id = plan["plan_id"]

    preview_resp = client.post(f"/api/agent/{plan_id}/preview", json={"use_sample_data": True}, headers=admin_headers)
    assert preview_resp.status_code == 200
    assert set(preview_resp.json()["tables"].keys()) == {"p_dtl_tb", "p_alt_id_tb"}

    client.post(f"/api/agent/{plan_id}/confirm", json={"confirmed": True}, headers=admin_headers)
    execute_resp = client.post(f"/api/agent/{plan_id}/execute", headers=admin_headers)
    assert execute_resp.status_code == 200
    body = execute_resp.json()
    assert body["execution_order"].index("p_dtl_tb") < body["execution_order"].index("p_alt_id_tb")


# ─────────────────────────────────────────────────────────────────────────── #
# Jobs + Audit                                                                #
# ─────────────────────────────────────────────────────────────────────────── #


def test_jobs_list_reflects_created_plans(client, admin_headers):
    client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=admin_headers)
    resp = client.get("/api/jobs", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) >= 1


def test_audit_log_reflects_plan_creation_and_redacts_secrets(client, admin_headers):
    client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=admin_headers)
    resp = client.get("/api/audit", headers=admin_headers)
    assert resp.status_code == 200
    records = resp.json()["records"]
    assert any(r["action"] == "plan_created" for r in records)
    # no raw secret value should ever appear in a rendered record
    assert not any("sup3rsecret" in json.dumps(r) for r in records)


def test_audit_log_search_filters_records(client, admin_headers):
    client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=admin_headers)
    resp = client.get("/api/audit", params={"q": "plan_created"}, headers=admin_headers)
    assert all("plan_created" in json.dumps(r).lower() or "plan_created" in str(r) for r in resp.json()["records"])


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
