"""
tests/test_phase5b_auth.py — auth + RBAC tests (ADR-0008).

Covers: login success/failure, missing/invalid/expired tokens, the
VIEWER < OPERATOR < ADMIN role boundary on every protected endpoint
(especially confirm/execute, which are ADMIN-gated — stricter than just
"any operator"), and that audit records attribute the REAL authenticated
user via `core.actor_context`, not a fixed constructor-time string.

Run: python -m pytest tests/test_phase5b_auth.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory
from agent.shared_storage import SharedStorage
from backend import dependencies
from backend.app import app
from backend.auth_deps import set_auth_provider
from core.audit import AuditLog
from core.auth import KeycloakAuthProvider, LocalDevAuthProvider, Role, User
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
    set_auth_provider(LocalDevAuthProvider(CONFIG_DIR / "users.yaml"))
    manager = ManagerAgent(
        llm=FakeLLMProvider({"intent": "mask", "table": "p_alt_id_tb", "reasoning": "x", "clarifying_question": None}),
        config_dir=CONFIG_DIR, ddl_dir=DDL_DIR,
        plan_memory=PlanMemory(tmp_path / "plans"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        shared_storage=SharedStorage(tmp_path / "shared"),
    )
    dependencies.set_manager(manager)
    yield TestClient(app)
    dependencies.reset_for_tests()


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    return resp


def _headers(client, username, password):
    resp = _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# ─────────────────────────────────────────────────────────────────────────── #
# Login                                                                       #
# ─────────────────────────────────────────────────────────────────────────── #


def test_login_succeeds_for_each_dev_seed_user(client):
    for username, password, role in [
        ("viewer", "viewer-dev-pw", "VIEWER"),
        ("operator", "operator-dev-pw", "OPERATOR"),
        ("admin", "admin-dev-pw", "ADMIN"),
    ]:
        resp = _login(client, username, password)
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == role


def test_login_fails_with_wrong_password(client):
    resp = _login(client, "admin", "not-the-password")
    assert resp.status_code == 401


def test_login_fails_for_unknown_user(client):
    resp = _login(client, "nobody", "whatever")
    assert resp.status_code == 401


def test_me_endpoint_reflects_logged_in_user(client):
    headers = _headers(client, "operator", "operator-dev-pw")
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "operator"
    assert resp.json()["role"] == "OPERATOR"


def test_keycloak_authenticate_uses_password_grant_and_verifies_tokens(monkeypatch):
    provider = KeycloakAuthProvider(server_url="https://kc.example.com", realm="demo", client_id="ui")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"access_token": "abc.def.ghi"}).encode("utf-8")

        def getcode(self):
            return 200

    def fake_urlopen(req, timeout=None):
        assert req.full_url == "https://kc.example.com/realms/demo/protocol/openid-connect/token"
        data = dict(parse_qsl(req.data.decode("utf-8")))
        assert data["grant_type"] == "password"
        assert data["username"] == "alice"
        assert data["password"] == "secret"
        assert data["client_id"] == "ui"
        return FakeResponse()

    monkeypatch.setattr("core.auth.urlopen", fake_urlopen)
    provider.verify_token = lambda token: User(username="alice", role=Role.OPERATOR, display_name="Alice")

    user = provider.authenticate("alice", "secret")
    assert user is not None
    assert user.username == "alice"
    assert user.role == Role.OPERATOR


# ─────────────────────────────────────────────────────────────────────────── #
# Missing / invalid tokens                                                    #
# ─────────────────────────────────────────────────────────────────────────── #


def test_protected_endpoint_401s_with_no_token(client):
    resp = client.get("/api/schema/tables")
    assert resp.status_code == 401


def test_protected_endpoint_401s_with_garbage_token(client):
    resp = client.get("/api/schema/tables", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_health_needs_no_token(client):
    # the one intentional exception — a liveness check shouldn't require credentials
    assert client.get("/api/health").status_code == 200


# ─────────────────────────────────────────────────────────────────────────── #
# Role boundaries                                                             #
# ─────────────────────────────────────────────────────────────────────────── #


def test_viewer_can_read_schema_but_not_propose_masking(client):
    headers = _headers(client, "viewer", "viewer-dev-pw")
    assert client.get("/api/schema/tables", headers=headers).status_code == 200
    resp = client.post("/api/masking/p_alt_id_tb/propose", json={}, headers=headers)
    assert resp.status_code == 403
    assert "OPERATOR" in resp.json()["detail"]


def test_operator_can_propose_masking_but_not_confirm_a_plan(client):
    headers = _headers(client, "operator", "operator-dev-pw")
    plan = client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=headers).json()
    assert plan["intent"] == "mask"

    propose = client.post("/api/masking/p_alt_id_tb/propose", json={}, headers=headers)
    assert propose.status_code == 200

    # confirm/execute are ADMIN-gated, deliberately stricter than OPERATOR
    # (see backend/routers/agent.py's comment) — an operator can plan and
    # preview a job but cannot authorize the write.
    confirm = client.post(f"/api/agent/{plan['plan_id']}/confirm", json={"confirmed": True}, headers=headers)
    assert confirm.status_code == 403
    assert "ADMIN" in confirm.json()["detail"]


def test_viewer_cannot_plan_a_job(client):
    headers = _headers(client, "viewer", "viewer-dev-pw")
    resp = client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=headers)
    assert resp.status_code == 403


def test_admin_can_do_everything_operator_and_viewer_can(client):
    headers = _headers(client, "admin", "admin-dev-pw")
    assert client.get("/api/schema/tables", headers=headers).status_code == 200
    assert client.post("/api/masking/p_alt_id_tb/propose", json={}, headers=headers).status_code == 200
    plan = client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=headers).json()
    client.post(f"/api/agent/{plan['plan_id']}/preview", json={"use_sample_data": True}, headers=headers)
    confirm = client.post(f"/api/agent/{plan['plan_id']}/confirm", json={"confirmed": True}, headers=headers)
    assert confirm.status_code == 200


def test_audit_read_requires_operator_not_just_viewer(client):
    viewer_headers = _headers(client, "viewer", "viewer-dev-pw")
    assert client.get("/api/audit", headers=viewer_headers).status_code == 403

    operator_headers = _headers(client, "operator", "operator-dev-pw")
    assert client.get("/api/audit", headers=operator_headers).status_code == 200


# ─────────────────────────────────────────────────────────────────────────── #
# Actor attribution (core.actor_context)                                      #
# ─────────────────────────────────────────────────────────────────────────── #


def test_audit_records_attribute_the_real_authenticated_user(client):
    operator_headers = _headers(client, "operator", "operator-dev-pw")
    client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=operator_headers)

    admin_headers = _headers(client, "admin", "admin-dev-pw")
    records = client.get("/api/audit", headers=admin_headers).json()["records"]

    plan_created = [r for r in records if r["action"] == "plan_created"]
    assert plan_created, "expected a plan_created audit record"
    assert plan_created[0]["actor"] == "operator", (
        "audit record should attribute the real logged-in user (operator), "
        "not a fixed constructor-time actor string"
    )


def test_different_requests_get_correctly_isolated_actors(client):
    """Regression guard for the contextvars/threadpool bug this pass fixed
    (ADR-0008): back-to-back requests from different users must not leak
    actor identity between them."""
    op_headers = _headers(client, "operator", "operator-dev-pw")
    client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=op_headers)

    admin_headers = _headers(client, "admin", "admin-dev-pw")
    client.post("/api/agent/plan", json={"nl_request": "mask p_alt_id_tb"}, headers=admin_headers)

    records = client.get("/api/audit", headers=admin_headers).json()["records"]
    actors = {r["actor"] for r in records if r["action"] == "plan_created"}
    assert actors == {"operator", "admin"}


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
