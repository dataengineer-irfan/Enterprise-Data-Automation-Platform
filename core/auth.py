"""
core/auth.py — authentication + role model.

Closes the Keycloak/RBAC gap flagged (and left open) in every ADR since
Phase 1. Mirrors the same pattern this project already uses twice —
`DatabasePort` (Phase 1) and `LLMProvider` (Phase 3): one abstract
interface, one free/local/testable default implementation, one
production-grade opt-in implementation that isn't live-tested here
because there's no live instance of it in this sandbox (Postgres/Oracle,
Ollama, and now Keycloak all follow the same shape for the same reason).

Roles are a simple ordered hierarchy — VIEWER < OPERATOR < ADMIN — not a
full Keycloak realm/client role graph. That's deliberate: this platform's
own actions reduce to "can read," "can plan/preview," and "can confirm and
execute a write," so a 3-level hierarchy models the real permission
boundary (Section 2.2 rule #2: only a confirmed plan may write) without
inventing structure the product doesn't need yet.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt
import yaml

DEV_JWT_SECRET_ENV = "AUTH_SECRET"
_INSECURE_DEV_DEFAULT_SECRET = "dev-only-insecure-secret-change-me"


class Role(IntEnum):
    VIEWER = 1     # read schema, masking previews, jobs, audit
    OPERATOR = 2   # + plan/preview a job
    ADMIN = 3      # + confirm/execute — the only role that can trigger a write

    @classmethod
    def from_str(cls, name: str) -> "Role":
        return cls[name.upper()]


@dataclass(frozen=True)
class User:
    username: str
    role: Role
    display_name: str


class AuthProvider(ABC):
    provider_name: str = "unknown"

    @abstractmethod
    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Returns a User on success, None on bad credentials — never raises
        for a wrong password (that's an expected outcome, not an error)."""

    @abstractmethod
    def issue_token(self, user: User) -> str: ...

    @abstractmethod
    def verify_token(self, token: str) -> Optional[User]:
        """Returns a User if the token is valid and unexpired, None otherwise."""


def _hash_password(password: str, salt: str) -> str:
    return hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()


class LocalDevAuthProvider(AuthProvider):
    """Default provider — no external identity system required. Reads
    `config/users.yaml` (username -> {salt, password_hash, role,
    display_name}), issues short-lived HS256 JWTs signed with
    `AUTH_SECRET` (env var; falls back to an INSECURE, clearly-labeled
    default for local dev only — never use the default outside a laptop).
    """

    provider_name = "local_dev"

    def __init__(self, users_path: Path, secret: Optional[str] = None, token_ttl_seconds: int = 3600) -> None:
        self._users_path = users_path
        self._secret = secret or os.environ.get(DEV_JWT_SECRET_ENV) or _INSECURE_DEV_DEFAULT_SECRET
        self._token_ttl = token_ttl_seconds
        self._users: dict[str, dict] = {}
        self._load_users()

    def _load_users(self) -> None:
        if not self._users_path.exists():
            self._users = {}
            return
        with open(self._users_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        self._users = data.get("users", {})

    def _save_users(self) -> None:
        """Persists the in-memory user dict back to disk, preserving the
        file's other top-level keys (the dev-password convenience note)
        rather than clobbering them."""
        existing = {}
        if self._users_path.exists():
            with open(self._users_path, "r", encoding="utf-8") as fh:
                existing = yaml.safe_load(fh) or {}
        existing["users"] = self._users
        with open(self._users_path, "w", encoding="utf-8") as fh:
            yaml.dump(existing, fh, sort_keys=False)

    def list_users(self) -> list[User]:
        return [
            User(username=name, role=Role.from_str(rec["role"]), display_name=rec.get("display_name", name))
            for name, rec in sorted(self._users.items())
        ]

    def create_user(self, username: str, password: str, role: Role, display_name: str = "") -> User:
        if username in self._users:
            raise ValueError(f"User '{username}' already exists.")
        if not password:
            raise ValueError("Password cannot be empty.")
        self._users[username] = generate_user_record(password, role, display_name or username)
        self._save_users()
        return User(username=username, role=role, display_name=display_name or username)

    def set_role(self, username: str, role: Role) -> User:
        record = self._users.get(username)
        if not record:
            raise KeyError(f"No such user: {username}")
        if role != Role.ADMIN and record["role"] == Role.ADMIN.name:
            remaining_admins = sum(1 for r in self._users.values() if r["role"] == Role.ADMIN.name)
            if remaining_admins <= 1:
                raise ValueError("Refusing to demote the last remaining ADMIN — the platform would become unmanageable.")
        record["role"] = role.name
        self._save_users()
        return User(username=username, role=role, display_name=record.get("display_name", username))

    def delete_user(self, username: str) -> None:
        record = self._users.get(username)
        if not record:
            raise KeyError(f"No such user: {username}")
        if record["role"] == Role.ADMIN.name:
            remaining_admins = sum(1 for r in self._users.values() if r["role"] == Role.ADMIN.name)
            if remaining_admins <= 1:
                raise ValueError("Refusing to delete the last remaining ADMIN — the platform would become unmanageable.")
        del self._users[username]
        self._save_users()

    def authenticate(self, username: str, password: str) -> Optional[User]:
        record = self._users.get(username)
        if not record:
            return None
        expected = record["password_hash"]
        actual = _hash_password(password, record["salt"])
        if not hmac.compare_digest(expected, actual):
            return None
        return User(username=username, role=Role.from_str(record["role"]), display_name=record.get("display_name", username))

    def issue_token(self, user: User) -> str:
        payload = {
            "sub": user.username,
            "role": user.role.name,
            "name": user.display_name,
            "iat": int(time.time()),
            "exp": int(time.time()) + self._token_ttl,
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def verify_token(self, token: str) -> Optional[User]:
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
        try:
            return User(username=payload["sub"], role=Role.from_str(payload["role"]), display_name=payload.get("name", payload["sub"]))
        except (KeyError, ValueError):
            return None


class KeycloakAuthProvider(AuthProvider):
    """Opt-in, production-grade provider — validates real Keycloak-issued
    OIDC tokens against the realm's JWKS endpoint. NOT exercised against a
    live Keycloak instance in this sandbox (no such instance is available
    here, same honesty note as `OracleDDLAdapter` re: live Oracle and
    `ClaudeProvider` re: a paid API key) — the RS256/JWKS verification
    logic below is standard OIDC and should work against a real realm, but
    treat it as unverified until it's actually been run against one.
    """

    provider_name = "keycloak"

    def __init__(self, server_url: Optional[str] = None, realm: Optional[str] = None, client_id: Optional[str] = None) -> None:
        self.server_url = server_url or os.environ.get("KEYCLOAK_URL")
        self.realm = realm or os.environ.get("KEYCLOAK_REALM")
        self.client_id = client_id or os.environ.get("KEYCLOAK_CLIENT_ID")
        if not all([self.server_url, self.realm, self.client_id]):
            raise RuntimeError(
                "KeycloakAuthProvider requires KEYCLOAK_URL, KEYCLOAK_REALM, and "
                "KEYCLOAK_CLIENT_ID. Set AUTH_PROVIDER=local to use the free local "
                "default instead (see core/auth.py's LocalDevAuthProvider)."
            )
        try:
            import jwt as _jwt  # noqa: F401
            from jwt import PyJWKClient  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("PyJWT with JWKS support is required for KeycloakAuthProvider.") from exc

    def _jwks_client(self):
        from jwt import PyJWKClient

        return PyJWKClient(f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/certs")

    def authenticate(self, username: str, password: str) -> Optional[User]:
        if not self.server_url or not self.realm or not self.client_id:
            raise RuntimeError(
                "KeycloakAuthProvider requires KEYCLOAK_URL, KEYCLOAK_REALM, and "
                "KEYCLOAK_CLIENT_ID."
            )

        token_url = f"{self.server_url.rstrip('/')}/realms/{self.realm}/protocol/openid-connect/token"
        payload = urlencode({
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": self.client_id,
        }).encode("utf-8")
        req = Request(token_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        access_token = body.get("access_token")
        if not access_token:
            return None
        return self.verify_token(access_token)

    def issue_token(self, user: User) -> str:
        raise NotImplementedError("Keycloak issues its own tokens — this backend only verifies them, never mints them.")

    def verify_token(self, token: str) -> Optional[User]:
        try:
            signing_key = self._jwks_client().get_signing_key_from_jwt(token)
            payload = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=self.client_id)
        except jwt.PyJWTError:
            return None
        roles = payload.get("realm_access", {}).get("roles", [])
        role = next((Role.from_str(r) for r in roles if r.upper() in Role.__members__), Role.VIEWER)
        return User(username=payload.get("preferred_username", payload.get("sub")), role=role, display_name=payload.get("name", ""))


def load_default_auth_provider(config_dir: Path) -> AuthProvider:
    provider = (os.environ.get("AUTH_PROVIDER") or "local").lower()
    if provider == "keycloak":
        return KeycloakAuthProvider()
    if provider == "local":
        return LocalDevAuthProvider(config_dir / "users.yaml")
    raise ValueError(f"Unknown AUTH_PROVIDER: {provider!r} (expected 'local' or 'keycloak')")


def generate_user_record(password: str, role: Role, display_name: str) -> dict:
    """Helper for seeding config/users.yaml (see backend/scripts or the
    inline generation this project's ADR shows) — never used at runtime,
    only to author the dev user list."""
    salt = secrets.token_hex(16)
    return {"salt": salt, "password_hash": _hash_password(password, salt), "role": role.name, "display_name": display_name}
