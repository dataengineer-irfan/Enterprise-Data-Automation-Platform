"""
backend/routers/admin.py — user management, ADMIN-only.

Closes the loop RBAC opened in ADR-0008: roles are enforced everywhere,
but the only way to actually manage *who has which role* was hand-editing
`config/users.yaml` and regenerating a salted hash by hand. This gives an
ADMIN a real screen for that instead.

Explicitly gated to `LocalDevAuthProvider` — if the deployment is
configured for `KeycloakAuthProvider` (AUTH_PROVIDER=keycloak), user
management belongs in that realm's own admin console, not here. Every
endpoint 400s with that explanation rather than silently no-op'ing or
raising an unhandled AttributeError.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth_deps import get_auth_provider, require_role
from core.auth import LocalDevAuthProvider, Role, User

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str
    display_name: str = ""


class UpdateRoleRequest(BaseModel):
    role: str


def _require_local_provider() -> LocalDevAuthProvider:
    provider = get_auth_provider()
    if not isinstance(provider, LocalDevAuthProvider):
        raise HTTPException(
            status_code=400,
            detail=f"User management isn't available for the '{provider.provider_name}' auth provider — "
                    "manage users in your identity provider's own admin console instead.",
        )
    return provider


def _user_out(user: User) -> dict:
    return {"username": user.username, "role": user.role.name, "display_name": user.display_name}


@router.get("")
def list_users(_user: User = Depends(require_role(Role.ADMIN))):
    provider = _require_local_provider()
    return {"users": [_user_out(u) for u in provider.list_users()]}


@router.post("")
def create_user(req: CreateUserRequest, _user: User = Depends(require_role(Role.ADMIN))):
    provider = _require_local_provider()
    try:
        role = Role.from_str(req.role)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown role: {req.role!r} (expected VIEWER, OPERATOR, or ADMIN)")
    try:
        created = provider.create_user(req.username, req.password, role, req.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _user_out(created)


@router.patch("/{username}")
def update_role(username: str, req: UpdateRoleRequest, _user: User = Depends(require_role(Role.ADMIN))):
    provider = _require_local_provider()
    try:
        role = Role.from_str(req.role)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown role: {req.role!r} (expected VIEWER, OPERATOR, or ADMIN)")
    try:
        updated = provider.set_role(username, role)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such user: {username}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _user_out(updated)


@router.delete("/{username}")
def delete_user(username: str, _user: User = Depends(require_role(Role.ADMIN))):
    provider = _require_local_provider()
    if username == _user.username:
        raise HTTPException(status_code=409, detail="You cannot delete your own account while signed in as it.")
    try:
        provider.delete_user(username)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such user: {username}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"deleted": username}
