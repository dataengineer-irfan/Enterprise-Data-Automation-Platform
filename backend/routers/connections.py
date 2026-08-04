from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth_deps import require_role, with_actor
from backend.dependencies import create_connection_record, get_connection_record, list_connection_records, test_connection_record
from core.auth import Role

router = APIRouter(prefix="/api/connections", tags=["connections"])


class ConnectionRequest(BaseModel):
    name: str
    env: str
    host: str
    port: int
    service_name: str | None = None
    sid: str | None = None
    username: str
    password: str
    ssl: bool = False
    kind: str = "oracle"


@router.get("")
def list_connections(_user=Depends(with_actor)):
    return {"connections": list_connection_records()}


@router.post("/test")
def test_connection(req: ConnectionRequest, _user=Depends(require_role(Role.ADMIN))):
    return test_connection_record(req.model_dump())


@router.get("/{connection_id}")
def get_connection(connection_id: str, _user=Depends(with_actor)):
    record = get_connection_record(connection_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such connection")
    return record


@router.post("")
def create_connection(req: ConnectionRequest, _user=Depends(require_role(Role.ADMIN))):
    record = create_connection_record(req.model_dump())
    return record
