"""
backend/routers/jobs.py — backs the Job Monitor screen.

A "job" in the UI's sense is just a Plan viewed through a different lens
— there's no separate job-tracking system, `PlanMemory` already is one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.auth_deps import with_actor
from backend.dependencies import get_manager, get_plan_memory

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(_user=Depends(with_actor)):
    manager = get_manager()
    plans = get_plan_memory().list_plans()
    jobs = []
    for plan in sorted(plans, key=lambda p: p.updated_at, reverse=True):
        tables = plan.tables if plan.fan_out else ([plan.table] if plan.table else [])
        order = manager.order_tables_for_execution(tables) if plan.fan_out and tables else tables
        jobs.append({
            "plan_id": plan.plan_id,
            "nl_request": plan.nl_request,
            "intent": plan.intent,
            "fan_out": plan.fan_out,
            "tables": tables,
            "execution_order": order,
            "status": plan.status.value,
            "subtask_count": len(plan.subtasks),
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        })
    return {"jobs": jobs}
