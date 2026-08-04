# Enterprise Data Platform — Full Architecture and Business Guide

## 1. Executive summary

This repository implements an enterprise-grade data platform control tower for provider and reference data operations. Its purpose is to help business and technical teams do four things safely and consistently:

1. Understand the schema and lineage of provider tables.
2. Discover sensitive fields and create masking rules.
3. Generate SQL previews from natural language requests.
4. Route execution through a formal approval and audit workflow.

The solution combines:

- a schema and relationship graph layer,
- validation and masking engines,
- agent-based orchestration,
- a FastAPI backend for controlled access,
- and a browser-based enterprise console for human-in-the-loop workflows.

This is not a generic chatbot. It is a governed operational workflow platform with role-based access, approvals, auditability, and controlled execution.

---

## 2. Why the platform exists

The business problem this platform addresses is common in regulated data environments:

- schemas are large and distributed,
- sensitive data must be masked or protected before exposure,
- analysts and operators need a guided workflow,
- SQL generation must be reviewed before execution,
- and every action should be traceable.

The platform is designed to reduce manual effort while increasing control.

### Business outcomes

- Faster understanding of table relationships and sensitive fields.
- Safer handling of personally identifying or regulated data.
- Standardized masking and validation workflows.
- Human approval before any sensitive write operation.
- Audit-ready execution records for governance and review.

---

## 3. What this project contains

### 3.1 Core platform layers

#### Data and metadata layer
Location:
- [core/database_port.py](core/database_port.py)
- [core/schema_graph.py](core/schema_graph.py)
- [core/glossary.py](core/glossary.py)
- [adapters/postgres_adapter.py](adapters/postgres_adapter.py)
- [adapters/oracle_ddl_adapter.py](adapters/oracle_ddl_adapter.py)

This layer is responsible for reading structural metadata from source definitions, resolving schema relationships, and exposing a normalized model that downstream workflows can use.

#### Validation and masking layer
Location:
- [core/validation.py](core/validation.py)
- [core/masking.py](core/masking.py)
- [core/rules_config.py](core/rules_config.py)

This layer provides the rule engine for:

- sensitive field detection,
- FK-aware masking consistency,
- validation of required columns and relationships,
- and rule-based transformations.

#### Agent orchestration layer
Location:
- [agent/manager.py](agent/manager.py)
- [agent/plan_memory.py](agent/plan_memory.py)
- [agent/shared_storage.py](agent/shared_storage.py)
- [agent/subagents](agent/subagents)

This layer coordinates the work. The manager converts natural-language requests into a structured plan and delegates to specialized agents.

#### FastAPI API and auth layer
Location:
- [backend/app.py](backend/app.py)
- [backend/auth_deps.py](backend/auth_deps.py)
- [backend/routers](backend/routers)
- [core/auth.py](core/auth.py)

This layer exposes controlled access to the platform through API routes, role checks, and identity propagation. It is the bridge between the UI and the agent workflow.

#### Frontend console layer
Location:
- [ui/console.jsx](ui/console.jsx)
- [console.jsx](console.jsx)

This is the control-plane interface for operational users. It presents schema, lineage, masking, approval, audit, and agent-driven views.

---

## 4. Role model and security posture

The project is built around a simple but powerful RBAC model.

### Roles

- VIEWER
  - Can inspect schema and platform information.

- OPERATOR
  - Can plan and preview actions.

- ADMIN
  - Can confirm and execute sensitive workflows.
  - Manages users and approvals.

### Security characteristics

- Authenticated access is enforced by the FastAPI dependencies.
- Auth sessions are token-based.
- Certain endpoints require admin-only access.
- Executes are gated behind confirmation.
- The project keeps a clear separation between planning and execution.

### Dev seed users
Source:
- [config/users.yaml](config/users.yaml)

Credentials:

- `viewer` / `viewer-dev-pw`
- `operator` / `operator-dev-pw`
- `admin` / `admin-dev-pw`

These are local dev credentials for the sandbox environment.

---

## 5. Agent architecture

The system uses a manager + subagent architecture.

### 5.1 manager
The main orchestrator is [agent/manager.py](agent/manager.py). It manages:

- natural-language request interpretation,
- plan creation,
- subtask fan-out,
- result consolidation,
- human confirmation,
- and execution orchestration.

### 5.2 subagents and responsibilities

#### schema_metadata_agent
Responsible for introspecting schema details and relationships.

#### profiling_mapping_agent
Responsible for profiling source data and mapping incoming files to target tables.

#### validation_agent
Responsible for validation checks and rule diagnostics.

#### correction_agent
Responsible for proposing corrections without applying direct writes.

#### masking_agent
Responsible for identifying sensitive fields and proposing masking strategies.

#### sql_generation_agent
Responsible for generating SQL statements from plan information.

#### execution_report_agent
Responsible for the final write/report path and is the only agent with write authority when properly gated.

### Why this matters to business users

This design makes the platform more transparent than a monolithic AI prompt. Roles are clearer, outputs are structured, and each stage can be reviewed before anything is executed.

---

## 6. Main UI screens

The frontend shell in [ui/console.jsx](ui/console.jsx) exposes the operational workflow as a set of screens.

### 6.1 Schema Explorer
Used to inspect table definitions, column metadata, PK/FK relationships, and table grouping.

### 6.2 Lineage Viewer
Used to trace relationships from a selected table to parent and child tables.

### 6.3 Masking Screen
Used to inspect sensitive columns and propose masking strategies.

### 6.4 SQL Editor
Used to generate or inspect SQL previews for operations on selected tables.

### 6.5 Agent Console
Used to send natural-language requests to the manager and review agent outputs in real time.

### 6.6 Approvals Dashboard
Used by admins to confirm or reject pending plans that require human approval.

### 6.7 Jobs / Ops Dashboard
Used to track backend operational metrics, plan counts, and pending approvals.

### 6.8 User Management
Used by admins to manage users and role assignments.

### 6.9 Audit View
Used to inspect historical activity and operational traceability.

---

## 7. Typical use cases

### Use case 1 — Schema understanding
A data governance analyst opens the Schema Explorer to understand table structure and relationships before doing any remediation work.

### Use case 2 — Sensitive-data review
A data steward opens the Masking screen and sees where sensitive columns, such as identifiers, tax-related, or credential-like data, exist.

### Use case 3 — Natural-language operations
A platform operator asks the Agent Console to:

- validate a table,
- mask a dataset,
- generate preview SQL,
- or inspect lineage.

The manager plans the work and delegates to specialized agents.

### Use case 4 — Controlled execution
When a plan requires write access, it enters an approval stage. Only an ADMIN can confirm and execute.

### Use case 5 — Audit and oversight
Ops and governance teams use the approvals and audit views to confirm who did what, when, and under which role.

---

## 8. End-to-end workflow

A common workflow looks like this:

1. User signs in through the console.
2. User selects a table or enters a natural-language request.
3. Manager creates a plan.
4. Appropriate subagents run in sequence.
5. Preview output is shown.
6. If execution is needed, the plan enters approval.
7. ADMIN confirms.
8. Execution report agent performs the write path.
9. Audit trail records the outcome.

This workflow is designed to keep the business user in control without giving them low-level system access.

---

## 9. API and integration model

The backend is exposed through FastAPI and organized into routers:

- [backend/routers/auth.py](backend/routers/auth.py)
- [backend/routers/schema.py](backend/routers/schema.py)
- [backend/routers/masking.py](backend/routers/masking.py)
- [backend/routers/agent.py](backend/routers/agent.py)
- [backend/routers/jobs.py](backend/routers/jobs.py)
- [backend/routers/audit.py](backend/routers/audit.py)
- [backend/routers/admin.py](backend/routers/admin.py)

The console communicates with these endpoints through the API client layer in [ui/console.jsx](ui/console.jsx).

### Important API entry points

- `/api/health`
  - Returns health and lock-down status.

- `/api/auth/login`
  - Authenticates the current user.

- `/api/schema/*`
  - Exposes schema metadata and table information.

- `/api/agent/*`
  - Plan, preview, confirm, execute, and report endpoints.

- `/api/masking/*`
  - Proposes or looks up masking actions.

- `/api/admin/*`
  - Admin-only user management endpoints.

---

## 10. Current runtime topology

### Backend service
The backend is launched with:

```powershell
Set-Location C:\Users\affra\Documents\ETS\enterprise_platform
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

This serves the FastAPI API on:

- `http://127.0.0.1:8000`

### Frontend static host
The frontend static server is launched with:

```powershell
Set-Location C:\Users\affra\Documents\ETS\enterprise_platform
.\.venv\Scripts\python.exe -m http.server 8007
```

This serves the repo root on:

- `http://127.0.0.1:8007/`

### Current verification note
The current codebase is serving the static workspace directory listing at port 8007. The actual console experience in this snapshot is backed by the React/JSX console implementation in [ui/console.jsx](ui/console.jsx), while the codebase still relies on the local bundle verification workflow in [.ui_verify](.ui_verify) to validate the production-like UI surface.

---

## 11. Login and validation steps

### Login accounts
Use the seeded accounts in [config/users.yaml](config/users.yaml):

- viewer / viewer-dev-pw
- operator / operator-dev-pw
- admin / admin-dev-pw

### Validation steps

1. Open the backend health endpoint:
   - `http://127.0.0.1:8000/api/health`

2. Authenticate with the admin account:
   - POST to `http://127.0.0.1:8000/api/auth/login`

3. Use the frontend endpoint to inspect the app shell:
   - `http://127.0.0.1:8007/`

4. Validate DBA and admin flows by logging in as `admin` and exploring:
   - Schema Explorer
   - Lineage
   - Agent Console
   - Approvals
   - User Management
   - Ops Dashboard

---

## 12. Verification status

This repository has been verified with fresh test evidence:

- `110 passed, 4 warnings` in the full test suite.
- Key Phase 5 API/CORS validation path also passed with `30 passed in 38.28s`.

The warnings are non-blocking and are related to environment-level dependency and dev-CORS behavior, not functional failures.

---

## 13. Business interpretation

The project is best understood as a business-facing data governance and execution cockpit.

It allows a company to:

- understand the data domain,
- protect sensitive information,
- operate AI-assisted planning,
- route critical actions through formal approval,
- and keep a governance trail for every step.

In short: it is a controlled, auditable enterprise data operations assistant.

---

## 14. Recommended next steps

1. Add a dedicated browser `index.html` entrypoint for the frontend console.
2. Bundle the React UI into a single browser-served artifact for direct end-user runtime.
3. Add real SSO/OIDC and stronger enterprise auth controls.
4. Promote the current dev-only CORS setup into an explicit deployment configuration.
5. Add deeper UI interaction tests for approvals and admin actions.

---

## 15. Final takeaway

This project is a business-grade governance and automation platform for provider data operations. It does not simply answer questions about a schema; it provides a controlled operating model for exploring data, making safe changes, generating SQL, securing sensitive information, and keeping every action reviewable.
