"""
backend/routers/auth.py — login + current-user endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth_deps import get_auth_provider, get_current_user
from core.auth import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _user_out(user: User) -> dict:
    return {"username": user.username, "role": user.role.name, "display_name": user.display_name}


@router.post("/login")
def login(req: LoginRequest):
    provider = get_auth_provider()
    user = provider.authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = provider.issue_token(user)
    return {"token": token, "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_out(user)
