"""
backend/routers/audit.py — backs the Audit Dashboard screen.

Reads straight through `AuditLog.read_all()` (Phase 1) — secrets are
already redacted at write time (`core/audit.py`'s `_redact`), so nothing
extra needs to happen here; the API can never leak a value the log itself
never stored.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.auth_deps import require_role
from backend.dependencies import get_audit_log
from core.auth import Role

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit_records(
    q: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    _user=Depends(require_role(Role.OPERATOR)),
    # OPERATOR+, not just any authenticated user (with_actor/VIEWER): the
    # audit trail names other users' actions by username, which is more
    # sensitive than schema metadata or job status — a deliberate,
    # documented choice, not the default "any reader" pattern the other
    # read endpoints use.
):
    records = get_audit_log().read_all()
    if q:
        needle = q.lower()
        records = [r for r in records if needle in str(r).lower()]
    return {"records": list(reversed(records))[:limit], "total": len(records)}
