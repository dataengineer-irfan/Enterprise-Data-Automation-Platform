from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth_deps import require_role, with_actor
from backend.dependencies import (
    create_workspace_record,
    discover_workspace_snapshot,
    generate_workspace_data,
    get_workspace_catalog,
    get_workspace_record,
    get_workspace_report,
    get_workspace_state,
    list_workspace_jobs,
    list_workspace_records,
    list_workspace_snapshots,
    get_workspace_metadata,
)
from core.auth import Role

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceRequest(BaseModel):
    name: str
    source_connection_id: str
    target_connection_id: str
    owner: str


@router.get("")
def list_workspaces(_user=Depends(with_actor)):
    return {"workspaces": list_workspace_records()}


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, _user=Depends(with_actor)):
    record = get_workspace_record(workspace_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such workspace")
    return record


@router.post("")
def create_workspace(req: WorkspaceRequest, _user=Depends(require_role(Role.ADMIN))):
    record = create_workspace_record(req.model_dump())
    return record


@router.post("/{workspace_id}/discover")
def discover_workspace(workspace_id: str, _user=Depends(require_role(Role.ADMIN))):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    snapshot = discover_workspace_snapshot(workspace_id)
    return {
        "status": "completed",
        "workspace_id": workspace_id,
        "snapshot_id": snapshot["snapshot_id"],
        "discovered": snapshot["summary"],
    }


@router.get("/{workspace_id}/snapshots")
def workspace_snapshots(workspace_id: str, _user=Depends(with_actor)):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    return {"snapshots": list_workspace_snapshots(workspace_id)}


@router.get("/{workspace_id}/metadata")
def workspace_metadata(workspace_id: str, _user=Depends(with_actor)):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    metadata = get_workspace_metadata(workspace_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="No snapshot metadata for workspace")
    return metadata


@router.get("/{workspace_id}/catalog")
def workspace_catalog(workspace_id: str, _user=Depends(with_actor)):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    catalog = get_workspace_catalog(workspace_id)
    if not catalog:
        raise HTTPException(status_code=404, detail="No catalog metadata for workspace")
    return catalog


@router.post("/{workspace_id}/generate")
def generate_workspace(workspace_id: str, _user=Depends(require_role(Role.ADMIN))):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    result = generate_workspace_data(workspace_id)
    return {
        "status": "completed",
        "workspace_id": workspace_id,
        "generated_rows": result["generated_rows"],
    }


@router.get("/{workspace_id}/report")
def workspace_report(workspace_id: str, _user=Depends(with_actor)):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    report = get_workspace_report(workspace_id)
    if not report:
        raise HTTPException(status_code=404, detail="No execution report for workspace")
    return report


@router.get("/{workspace_id}/state")
def workspace_state(workspace_id: str, _user=Depends(with_actor)):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    state = get_workspace_state(workspace_id)
    if not state:
        raise HTTPException(status_code=404, detail="No state available for workspace")
    return state


@router.get("/{workspace_id}/jobs")
def workspace_jobs(workspace_id: str, _user=Depends(with_actor)):
    workspace = get_workspace_record(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="No such workspace")
    return {"jobs": list_workspace_jobs(workspace_id)}
