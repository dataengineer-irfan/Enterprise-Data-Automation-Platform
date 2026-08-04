"""
backend/routers/masking.py — backs the Masking Designer screen.

Sample rows for the before/after preview are generated on the fly
(`backend/sample_rows.py`) rather than requiring a hand-authored CSV per
table, so this works for any table with sensitive columns, not just the
one (`p_alt_id_tb`) that has a `samples/*.csv` file from Phase 2's CLI work.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth_deps import require_role, with_actor
from backend.dependencies import cache_proposed_policy, get_manager, get_proposed_policy
from backend.sample_rows import generate_sample_rows
from core.auth import Role
from core.masking import MaskingRule, MaskStrategy

router = APIRouter(prefix="/api/masking", tags=["masking"])


class ProposeRequest(BaseModel):
    salt_prefix: str = "auto"


class OverrideRuleRequest(BaseModel):
    column: str
    strategy: str
    salt: str | None = None


@router.get("/tables")
def list_sensitive_tables(_user=Depends(with_actor)):
    manager = get_manager()
    results = []
    for name in manager.list_table_names():
        table = manager.get_table(name)
        classify = manager.dispatch_subagent("masking_agent", {"action": "classify_sensitivity", "table": table})
        if classify.summary.get("count", 0) > 0:
            results.append({"table": name, "sensitive_columns": classify.summary["sensitive_columns"]})
    return {"tables": results}


@router.post("/{table_name}/propose")
def propose_masking(table_name: str, req: ProposeRequest, _user=Depends(require_role(Role.OPERATOR))):
    manager = get_manager()
    table = manager.get_table(table_name)
    if not table:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table_name}")

    result = manager.dispatch_subagent(
        "masking_agent", {"action": "propose_masking_rule", "table": table, "salt_prefix": req.salt_prefix},
    )
    policy = result.runtime_payload
    cache_proposed_policy(table_name, policy)
    detail = manager.storage.read_detail(result.detail_pointer.pointer)
    return {"table": table_name, "policy_name": policy.name, "rules": detail["rules"]}


@router.post("/{table_name}/override")
def override_rule(table_name: str, req: OverrideRuleRequest, _user=Depends(require_role(Role.OPERATOR))):
    """Lets a human edit one proposed rule before preview/apply — the
    Masking Agent proposes, a person can always override (never silently
    applies an unreviewed choice)."""
    policy = get_proposed_policy(table_name)
    if not policy:
        raise HTTPException(status_code=404, detail="No proposed policy for this table yet — call /propose first.")
    try:
        strategy = MaskStrategy(req.strategy)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy}")
    existing = policy.get_rule(req.column)
    policy.set_rule(MaskingRule(
        column=req.column, strategy=strategy,
        salt=req.salt or (existing.salt if existing else f"auto-{table_name}"),
        faker_provider=existing.faker_provider if existing else None,
    ))
    return {"table": table_name, "column": req.column, "strategy": strategy.value}


@router.get("/{table_name}/preview")
def preview_masking(table_name: str, _user=Depends(with_actor)):
    manager = get_manager()
    table = manager.get_table(table_name)
    if not table:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table_name}")
    policy = get_proposed_policy(table_name)
    if not policy:
        raise HTTPException(status_code=404, detail="No proposed policy for this table yet — call /propose first.")

    rows = generate_sample_rows(table, count=3)
    result = manager.dispatch_subagent("masking_agent", {"action": "apply_masking_dry_run", "rows": rows, "policy": policy, "limit": 3})
    detail = manager.storage.read_detail(result.detail_pointer.pointer)
    return {"table": table_name, "sample_rows": rows, "preview": detail}
