"""
backend/routers/agent.py — backs the Agent Console screen.

Maps HTTP verbs onto `ManagerAgent`'s six-step workflow one-for-one rather
than inventing a different shape: POST /plan, POST /{id}/preview,
POST /{id}/confirm, POST /{id}/execute, GET /{id}/report. Each step is a
separate request because each step in the real workflow is a separate,
independently-audited action (Section 2.2 rule #1) — collapsing them into
one call would misrepresent what the backend actually enforces.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth_deps import require_role, with_actor
from backend.dependencies import (
    cache_policy,
    cache_rows,
    get_cached_policy,
    get_cached_rows,
    get_manager,
    get_plan_memory,
    rows_to_temp_csv,
)
from backend.sample_rows import generate_sample_rows
from core.auth import Role

router = APIRouter(prefix="/api/agent", tags=["agent"])


class PlanRequest(BaseModel):
    nl_request: str


class PreviewRequest(BaseModel):
    rows: list[dict] | None = None          # single-table plans
    rows_by_table: dict[str, list[dict]] | None = None  # fan-out plans
    mask: bool = False                        # fan-out only: also run masking preview
    use_sample_data: bool = True             # if no rows given, synthesize plausible ones


class ConfirmRequest(BaseModel):
    confirmed: bool


def _plan_out(plan) -> dict:
    return plan.to_dict()


@router.get("/roster")
def roster(_user=Depends(with_actor)):
    return {"agents": get_manager().list_agents()}


@router.post("/plan")
def create_plan(req: PlanRequest, _user=Depends(require_role(Role.OPERATOR))):
    plan = get_manager().plan(req.nl_request)
    return _plan_out(plan)


@router.get("/{plan_id}")
def read_plan(plan_id: str, _user=Depends(with_actor)):
    plan = get_plan_memory().read(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="No such plan")
    return _plan_out(plan)


@router.post("/{plan_id}/preview")
def preview_plan(plan_id: str, req: PreviewRequest, _user=Depends(require_role(Role.OPERATOR))):
    manager = get_manager()
    plan = get_plan_memory().read(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="No such plan")
    if plan.intent == "unclear":
        raise HTTPException(status_code=400, detail=f"Plan is unresolved: {plan.clarifying_question}")

    if plan.fan_out:
        rows_by_table = req.rows_by_table or {}
        csv_paths = {}
        for table_name in plan.tables:
            rows = rows_by_table.get(table_name)
            if rows is None and req.use_sample_data:
                rows = generate_sample_rows(manager.get_table(table_name))
            csv_paths[table_name] = rows_to_temp_csv(rows or [])
            cache_rows(f"{plan_id}:{table_name}", rows or [])
        preview = manager.run_fan_out_preview(plan, csv_paths, mask=req.mask)
        policies = preview.pop("_policies", {})
        for table_name, policy in policies.items():
            cache_policy(f"{plan_id}:{table_name}", policy)
        return preview

    rows = req.rows
    if rows is None and req.use_sample_data:
        rows = generate_sample_rows(manager.get_table(plan.table))
    cache_rows(plan_id, rows or [])
    csv_path = rows_to_temp_csv(rows or [])
    preview = manager.preview(plan, csv_path)
    policy = preview.pop("_policy", None)
    if policy:
        cache_policy(plan_id, policy)
    return preview


@router.post("/{plan_id}/confirm")
def confirm_plan(plan_id: str, req: ConfirmRequest, _user=Depends(require_role(Role.ADMIN))):
    # ADMIN-gated, not just OPERATOR: this is the human act that flips a plan
    # to CONFIRMED, the one status ExecutionReportAgent's own independent
    # gate (ADR-0004/0006) treats as write-authorizing — rule #2's "only a
    # confirmed plan may write" now also means "only an ADMIN may confirm it."
    manager = get_manager()
    plan = get_plan_memory().read(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="No such plan")
    confirmed = manager.request_human_confirmation(plan, ask=lambda _msg: req.confirmed)
    return {"plan_id": plan_id, "confirmed": confirmed, "status": plan.status.value}


@router.post("/{plan_id}/execute")
def execute_plan(plan_id: str, _user=Depends(require_role(Role.ADMIN))):
    manager = get_manager()
    plan = get_plan_memory().read(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="No such plan")

    try:
        if plan.fan_out:
            csv_paths = {}
            policies = {}
            for table_name in plan.tables:
                rows = get_cached_rows(f"{plan_id}:{table_name}")
                csv_paths[table_name] = rows_to_temp_csv(rows)
                policy = get_cached_policy(f"{plan_id}:{table_name}")
                if policy:
                    policies[table_name] = policy
            result = manager.execute_fan_out(plan, csv_paths, policies=policies)
        else:
            rows = get_cached_rows(plan_id)
            csv_path = rows_to_temp_csv(rows)
            policy = get_cached_policy(plan_id)
            result = manager.execute(plan, csv_path, ruleset=policy)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    return result


@router.get("/{plan_id}/report")
def report_plan(plan_id: str, _user=Depends(with_actor)):
    return {"plan_id": plan_id, "report": get_manager().report(plan_id)}
