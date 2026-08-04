"""
backend/auth_deps.py — FastAPI dependencies for authentication + RBAC.

Three dependencies every router uses:
  - `get_current_user`: validates the `Authorization: Bearer <token>` header,
    401s if missing/invalid/expired.
  - `require_role(min_role)`: a dependency factory — 403s if the
    authenticated user's role is below `min_role`. This is the human-side
    complement to rule #2 (only ADMIN may hit confirm/execute endpoints).
  - `with_actor`: binds the authenticated username into
    `core.actor_context` for the duration of the request, so every audit
    record written during that request attributes the real user, not a
    fixed "api" string.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.actor_context import reset_current_actor, set_current_actor
from core.auth import AuthProvider, Role, User, load_default_auth_provider

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

_bearer = HTTPBearer(auto_error=False)
_auth_provider: AuthProvider | None = None


def get_auth_provider() -> AuthProvider:
    global _auth_provider
    if _auth_provider is None:
        _auth_provider = load_default_auth_provider(CONFIG_DIR)
    return _auth_provider


def set_auth_provider(provider: AuthProvider) -> None:
    """Test-only injection point, same pattern as backend.dependencies.set_manager."""
    global _auth_provider
    _auth_provider = provider


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token. POST /api/auth/login first.")
    user = get_auth_provider().verify_token(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    return user


def require_role(min_role: Role):
    """Role check + actor-context binding in one dependency: every
    authenticated request gets its username bound into
    `core.actor_context` for its duration (cheap, and means any audit
    record written anywhere during the request — present or future code —
    is automatically attributed correctly, rather than requiring every
    call site to remember to add a second `with_actor` dependency).

    This MUST be an async generator, not a sync one. FastAPI runs sync
    generator dependencies via `run_in_threadpool` — separately for the
    pre-yield half and the post-yield (`finally`) half — and each call can
    land in a different worker thread with its own copied `contextvars`
    Context. A `Token` created in one thread's Context cannot be used to
    `.reset()` a different Context, which raised
    `ValueError: <Token ...> was created in a different Context` under
    real concurrent test/request load. An async generator stays on the
    single asyncio task for the whole request, so the Context — and the
    Token — never crosses a thread boundary. Caught by this project's own
    test suite exercising the real dependency under FastAPI's TestClient,
    not by manual inspection — recorded in ADR-0008."""

    async def _check(user: User = Depends(get_current_user)):
        if user.role < min_role:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {min_role.name}+ role; {user.username} has {user.role.name}.",
            )
        token = set_current_actor(user.username)
        try:
            yield user
        finally:
            reset_current_actor(token)

    return _check


# Convenience alias: any authenticated user, actor bound, no elevated role required.
with_actor = require_role(Role.VIEWER)
