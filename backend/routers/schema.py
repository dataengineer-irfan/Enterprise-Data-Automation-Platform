"""
backend/routers/schema.py — backs the Schema Explorer screen.

Every read here goes through `ManagerAgent.dispatch_subagent("schema_metadata_agent", ...)`
rather than calling the adapter/graph directly — a deliberate choice so the
API surface exercises the real dispatch/audit path (Section 2.1/2.2 rule
#3: every tool call is audited) instead of quietly bypassing it for reads.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.auth_deps import with_actor
from backend.dependencies import get_manager

router = APIRouter(prefix="/api/schema", tags=["schema"])


@router.get("/tables")
def list_tables(_user=Depends(with_actor)):
    manager = get_manager()
    tables = [manager.get_table(name) for name in manager.list_table_names()]
    return {
        "tables": [
            {
                "name": t.name,
                "column_count": len(t.columns),
                "primary_key": t.primary_key,
                "fk_count": len(t.foreign_keys),
                "required_for_active_status": t.required_for_active_status,
            }
            for t in tables
        ]
    }


@router.get("/tables/{table_name}")
def get_table(table_name: str, _user=Depends(with_actor)):
    manager = get_manager()
    result = manager.dispatch_subagent("schema_metadata_agent", {"action": "introspect_schema", "table": table_name})
    if not result.success:
        raise HTTPException(status_code=404, detail=result.error)
    detail = manager.storage.read_detail(result.detail_pointer.pointer)

    fk_result = manager.dispatch_subagent("schema_metadata_agent", {"action": "build_fk_graph", "table": table_name})
    fk_detail = manager.storage.read_detail(fk_result.detail_pointer.pointer) if fk_result.success else {"parents": [], "children": []}

    return {
        **detail,
        # SchemaGraph (Phase 1) stores table names in relationships_verified.yaml's
        # native UPPER_CASE; every other identifier this API returns (table names,
        # column names) is lower_case, matching the DDL-converted metadata — so
        # normalize here rather than leaking two casing conventions to callers.
        "parents": [p.lower() for p in fk_detail.get("parents", [])],
        "children": [c.lower() for c in fk_detail.get("children", [])],
    }


@router.get("/glossary/{term}")
def resolve_glossary_term(term: str, _user=Depends(with_actor)):
    manager = get_manager()
    result = manager.dispatch_subagent("schema_metadata_agent", {"action": "resolve_glossary_term", "term": term})
    if not result.success:
        raise HTTPException(status_code=404, detail=result.error)
    return result.summary
