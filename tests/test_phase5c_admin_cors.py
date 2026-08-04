"""
tests/test_phase5c_admin_cors.py — Phase 5c: user management (ADMIN-only)
and CORS hardening (ADR-0009).

Run: python -m pytest tests/test_phase5c_admin_cors.py -v
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from backend import auth_deps
from backend.app import app
from core.auth import LocalDevAuthProvider, Role

ROOT = Path(__file__).resolve().parents[1]
REAL_USERS_FILE = ROOT / "config" / "users.yaml"


@pytest.fixture()
def client(tmp_path):
    """Uses a TEMP COPY of the real users.yaml so mutation tests (create/
    delete/role changes) never touch the actual dev seed file — the same
    care taken when these methods were first hand-verified in this session."""
    users_copy = tmp_path / "users.yaml"
    shutil.copy(REAL_USERS_FILE, users_copy)
    auth_deps.set_auth_provider(LocalDevAuthProvider(users_copy))
    yield TestClient(app)
    auth_deps.set_auth_provider(None)


def _login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────── #
# Admin user management                                                       #
# ─────────────────────────────────────────────────────────────────────────── #


def test_list_users_requires_admin(client):
    viewer_token = _login(client, "viewer", "viewer-dev-pw")
    resp = client.get("/api/admin/users", headers=_headers(viewer_token))
    assert resp.status_code == 403


def test_admin_can_list_users(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.get("/api/admin/users", headers=_headers(admin_token))
    assert resp.status_code == 200
    usernames = {u["username"] for u in resp.json()["users"]}
    assert {"viewer", "operator", "admin"} <= usernames
    # never leak password hash/salt to the API surface
    assert all("password_hash" not in u and "salt" not in u for u in resp.json()["users"])


def test_admin_can_create_and_authenticate_new_user(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.post(
        "/api/admin/users", headers=_headers(admin_token),
        json={"username": "newop", "password": "newop-pass-123", "role": "OPERATOR", "display_name": "New Operator"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"username": "newop", "role": "OPERATOR", "display_name": "New Operator"}

    # the created user can actually log in with the password just set
    new_token = _login(client, "newop", "newop-pass-123")
    assert new_token


def test_create_duplicate_user_409s(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.post(
        "/api/admin/users", headers=_headers(admin_token),
        json={"username": "viewer", "password": "x", "role": "VIEWER"},
    )
    assert resp.status_code == 409


def test_create_user_unknown_role_400s(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.post(
        "/api/admin/users", headers=_headers(admin_token),
        json={"username": "someone", "password": "x", "role": "SUPERUSER"},
    )
    assert resp.status_code == 400


def test_admin_can_update_role_and_it_persists(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    client.post(
        "/api/admin/users", headers=_headers(admin_token),
        json={"username": "promoteme", "password": "x-pass-123", "role": "VIEWER"},
    )
    resp = client.patch("/api/admin/users/promoteme", headers=_headers(admin_token), json={"role": "ADMIN"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "ADMIN"

    # the promoted user can now do admin-only things
    promoted_token = _login(client, "promoteme", "x-pass-123")
    list_resp = client.get("/api/admin/users", headers=_headers(promoted_token))
    assert list_resp.status_code == 200


def test_cannot_demote_last_admin(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.patch("/api/admin/users/admin", headers=_headers(admin_token), json={"role": "VIEWER"})
    assert resp.status_code == 409


def test_cannot_delete_last_admin(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.delete("/api/admin/users/admin", headers=_headers(admin_token))
    assert resp.status_code == 409


def test_cannot_delete_own_account(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    client.post(
        "/api/admin/users", headers=_headers(admin_token),
        json={"username": "second-admin", "password": "x-pass-123", "role": "ADMIN"},
    )
    second_token = _login(client, "second-admin", "x-pass-123")
    resp = client.delete("/api/admin/users/second-admin", headers=_headers(second_token))
    assert resp.status_code == 409
    assert "own account" in resp.json()["detail"]


def test_admin_can_delete_a_different_user(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    client.post(
        "/api/admin/users", headers=_headers(admin_token),
        json={"username": "throwaway", "password": "x-pass-123", "role": "VIEWER"},
    )
    resp = client.delete("/api/admin/users/throwaway", headers=_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "throwaway"}

    # actually gone — can no longer log in
    login_resp = client.post("/api/auth/login", json={"username": "throwaway", "password": "x-pass-123"})
    assert login_resp.status_code == 401


def test_delete_unknown_user_404s(client):
    admin_token = _login(client, "admin", "admin-dev-pw")
    resp = client.delete("/api/admin/users/not-a-real-user", headers=_headers(admin_token))
    assert resp.status_code == 404


def test_real_users_yaml_never_touched_by_these_tests():
    """Belt-and-suspenders: confirm the fixture really is using a temp copy,
    not the real dev seed file, by checking the real file's user count is
    still exactly 3 after the whole module has run mutation tests."""
    import yaml

    with open(REAL_USERS_FILE) as fh:
        data = yaml.safe_load(fh)
    assert set(data["users"].keys()) == {"viewer", "operator", "admin"}


# ─────────────────────────────────────────────────────────────────────────── #
# CORS hardening (ADR-0009)                                                   #
# ─────────────────────────────────────────────────────────────────────────── #


def test_health_reports_cors_lockdown_status_when_unset(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    import backend.app as app_module
    importlib.reload(app_module)
    client = TestClient(app_module.app)
    body = client.get("/api/health").json()
    assert body["cors_locked_down"] is False  # default-open, but now OBSERVABLE, not just a comment


def test_health_reports_cors_locked_down_when_configured(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://console.example.com")
    import backend.app as app_module
    importlib.reload(app_module)
    client = TestClient(app_module.app)
    body = client.get("/api/health").json()
    assert body["cors_locked_down"] is True
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    importlib.reload(app_module)  # restore default state for any test that runs after this one


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
