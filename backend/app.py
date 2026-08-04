"""
backend/app.py — FastAPI entrypoint. Run with:

    uvicorn backend.app:app --reload --port 8000

Then point ui/console.jsx at http://localhost:8000. Log in via
POST /api/auth/login first (see config/users.yaml for the dev seed
accounts) — every route except /api/health and /api/auth/login now
requires a bearer token (see ADR-0008).

CORS origin allowlisting (ADR-0009): controlled by CORS_ALLOWED_ORIGINS,
a comma-separated list of allowed origins. Unset -> defaults to a local-dev
allowlist that includes localhost variants so the existing "just run uvicorn
and point the UI at it" workflow keeps working without extra setup, while
still avoiding the old "accept any origin" default in real deployments.
The chosen default is surfaced at process startup (a `warnings.warn`, visible
in any real log aggregator) AND at runtime via `GET /api/health`'s
`cors_locked_down` field, so a deployment can be verified locked-down with
one curl call rather than by reading source. Set CORS_ALLOWED_ORIGINS to a
real comma-separated origin list before this runs anywhere but a laptop.
"""
from __future__ import annotations

import os
import sys
import warnings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.auth_deps import get_auth_provider
from backend.dependencies import get_manager, get_plan_memory, get_runtime_ops_metrics
from backend.routers import admin, agent, audit, auth, connections, jobs, masking, schema, workspaces

app = FastAPI(
    title="Provider Data Platform API",
    description="HTTP bridge between ui/console.jsx and the Phase 1-4 Python backend (Sections 1-4 of the platform spec).",
    version="0.8.0",
)

_cors_env = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
_default_local_origins = [
    "http://localhost:3007",
    "http://127.0.0.1:3007",
    "http://localhost:8007",
    "http://127.0.0.1:8007",
]
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else _default_local_origins
_cors_locked_down = _cors_origins != _default_local_origins

_running_under_pytest = "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") is not None

if not _cors_locked_down and not _running_under_pytest:
    warnings.warn(
        "CORS_ALLOWED_ORIGINS is not set — using a local-dev allowlist for localhost "
        "variants only. Set CORS_ALLOWED_ORIGINS to a comma-separated allowlist "
        "(e.g. 'https://your-console.example.com') before this runs anywhere but a laptop. "
        "Check GET /api/health -> cors_locked_down to confirm this was actually set.",
        stacklevel=1,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(connections.router)
app.include_router(schema.router)
app.include_router(masking.router)
app.include_router(agent.router)
app.include_router(jobs.router)
app.include_router(audit.router)
app.include_router(workspaces.router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi import Response
    return Response(status_code=204)


@app.get("/api/health")
def health():
    manager = get_manager()
    plans = get_plan_memory().list_plans()
    pending_approvals = sum(1 for plan in plans if plan.status.value == "awaiting_confirmation")
    ops_metrics = get_runtime_ops_metrics()
    return {
        "status": "ok",
        "cors_locked_down": _cors_locked_down,
        "ops": {
            "auth_provider": get_auth_provider().provider_name,
            "pending_approvals": pending_approvals,
            "table_count": len(manager.list_table_names()),
            "plan_count": len(plans),
            "workspace_count": ops_metrics["workspace_count"],
            "snapshot_count": ops_metrics["snapshot_count"],
            "job_count": ops_metrics["job_count"],
        },
    }
