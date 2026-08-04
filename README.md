# Enterprise Data Platform — Phase 1 + 2 + 3 + 4 + 5 (Provider Module)

Scope so far: **Phase 1 (Foundation)** + **Phase 2 (Validation & Masking)**
+ **Phase 3 (single-agent NL layer)** + **Phase 4 (multi-agent fan-out)**
+ **Phase 5 (Enterprise UI, FastAPI bridge, UI-API wiring, auth/RBAC, CORS
hardening, user management, and an Approval Dashboard)**. Every API route
except `/api/health` and `/api/auth/login` requires a bearer token;
confirm/execute require ADMIN specifically; CORS lockdown is observable at
`GET /api/health`; an ADMIN can manage users from a real screen; and — the
latest fix — a plan pending confirmation is now discoverable and
actionable from *any* session, not just the one that created it (a real
gap, not a spec checkbox — see `docs/adr/0010-approval-dashboard.md`).
Full decision trail: `docs/adr/0001-provider-module-foundation.md`
through `docs/adr/0013-dba-console.md`.

## UI + API together

```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8000
```

Open `ui/console.jsx` — it defaults to `http://localhost:8007`, checks
`/api/health`, and shows **Live** once connected. If live, you'll land on
a sign-in screen: dev seed accounts are `viewer`/`operator`/`admin`
(passwords `<username>-dev-pw`, see `config/users.yaml`). Role matters:
`viewer` can look around, `operator` can plan/preview/propose, `admin` is
required to confirm/execute anything — from Agent Console *or* the
Approvals screen (which now shows a live pending-count badge in the
sidebar) — and to manage users. A session expiring mid-use now returns
you to the login screen with a clear reason, rather than quietly failing.
`API Explorer` in the sidebar links straight to FastAPI's own `/docs`.
Command palette: `Cmd/Ctrl+K`. Dark/light toggle: sun/moon icon.

Before deploying anywhere but a laptop: set `CORS_ALLOWED_ORIGINS` (see
`backend/app.py`) — `GET /api/health`'s `cors_locked_down` field tells you
whether you actually did.

## What's here

```
core/
  database_port.py     # DatabasePort ABC + engine-agnostic data contracts
  schema_graph.py       # FK/PK graph from the VERIFIED relationships file
  glossary.py            # business-term / valid-value lookup
  audit.py                # append-only audit log, auto-redacts secrets
  validation.py            # Phase 2: pluggable ValidationRule + ValidationEngine
  masking.py                 # Phase 2: FK-aware MaskingEngine + strategies
  rules_config.py              # loads `sensitive: true` flags for the masking classifier
  llm_provider.py                # Phase 3: pluggable LLM provider (Ollama default / Claude opt-in)

agent/
  plan_memory.py         # Phase 3/4: persistent plan store (JSON files; DB/Redis-swappable)
  manager.py               # Phase 3/4: Manager — linear (single-table) + fan-out (multi-table) workflows
  shared_storage.py          # Phase 4: condensed-result handoff detail store
  subagents/
    base.py                    # Subagent ABC + SubagentResult (condensed summary + detail pointer)
    schema_metadata_agent.py
    profiling_mapping_agent.py
    validation_agent.py
    correction_agent.py
    masking_agent.py
    sql_generation_agent.py
    execution_report_agent.py    # the ONLY subagent with write authority

adapters/
  postgres_adapter.py    # live Postgres, wraps the existing DatabaseManager/
                          # SQLExecutor; enforces source-only write refusal
  oracle_ddl_adapter.py   # introspects the real Oracle DDL export (no live
                          # Oracle needed in this sandbox); PK/FK reconciled
                          # against relationships_verified.yaml

config/
  relationships_verified.yaml  # ground-truth FK/PK export (from project KB)
  generation_rules.yaml         # column generation rules incl. sensitive flags
  glossary.csv, reference_data.csv

input/ddl/*.sql           # the real 109-table Oracle Provider DDL export
samples/                    # example CSV + masking-ruleset YAML for the CLI
cli.py                        # Phase 2 CLI: validate / mask / detect-sensitive / fk-check
chat.py                         # Phase 3 REPL: NL -> plan -> preview -> confirm -> execute -> report

backend/
  app.py                  # FastAPI app — CORS wide open, dev-only, flagged
  dependencies.py           # shared ManagerAgent/PlanMemory/AuditLog singletons
  auth_deps.py                # with_actor / require_role(Role) FastAPI dependencies
  sample_rows.py              # on-the-fly plausible rows for any table (Faker-backed)
  routers/
    schema.py, masking.py, agent.py, jobs.py, audit.py, auth.py, admin.py

core/
  auth.py                  # Role/User/LocalDevAuthProvider — HMAC-hashed passwords, JWT issuance
  actor_context.py           # contextvars-based per-request actor tracking (fixes a real
                              # cross-request audit-attribution race — see ADR-0008)

ui/
  console.jsx              # Phase 5 UI — dual-mode (live API + login gate, or demo mock data)
                             # 9 screens: Schema, Lineage, Masking, DBA Console, Agent Console, Approvals, Jobs, Audit, User Management
  test_phase1_foundation.py            # 6 tests
  test_phase2_validation_masking.py     # 16 tests
  test_phase3_agent.py                    # 11 tests (FakeLLMProvider + one real-Ollama-unreachable test)
  test_phase4_subagents.py                  # 8 tests (each subagent in isolation)
  test_phase4_orchestration.py                # 14 tests (fan-out, condensed handoff, write-authority gating, correction loop)
  test_phase5_api.py                            # 16 tests (full HTTP workflow, fan-out, jobs, audit — auth-aware)
  test_phase5b_auth.py                            # 14 tests (login, RBAC, actor-context isolation)
  test_phase5c_admin_cors.py                        # 14 tests (user management, CORS observability)
  test_phase5d_approvals.py                            # 2 tests (real cross-session confirm/execute proof)

# carried over unmodified from the uploaded converter project —
# not yet wired into core/ or the CLI above:
ddl_converter.py, datatype_mapper.py, db.py, sql_executor.py,
data_generator.py, pattern_analyzer.py, data_loader.py, converter.py
```

## Run the tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v      # 101/101, no live DB and no live Ollama needed
```

A new Phase 5 UI regression test now also verifies the `ui/console.jsx`
bundle path and server-side render using `.ui_verify`'s `esbuild` +
`react-dom/server`. The regression test is implemented in
`tests/test_phase5h_ui_bundle_ssr.py`, so the UI shell is checked for
buildable markup as well.

No database connection required — the Oracle path introspects static DDL
files, and the Postgres source-only-refusal test fails before ever opening
a socket. The agent tests use a `FakeLLMProvider`; only one test touches a
real (deliberately unreachable) `OllamaProvider` socket, to prove the
graceful-failure path. The API tests use FastAPI's `TestClient` with the
same `FakeLLMProvider` pattern injected via `backend.dependencies.set_manager`.

## Try the Phase 2 CLI

```bash
python cli.py detect-sensitive --table p_alt_id_tb
python cli.py validate --table p_alt_id_tb --input samples/p_alt_id_tb.csv
python cli.py mask --table p_alt_id_tb --input samples/p_alt_id_tb.csv \
    --ruleset samples/p_alt_id_ruleset.yaml --dry-run
python cli.py fk-check --manifest samples/fk_manifest.yaml
```

## Try the Phase 3 agent chat (needs a real Ollama running)

```bash
# one-time setup:
#   https://ollama.com  ->  ollama pull qwen2:7b  ->  ollama serve

cp .env.example .env   # defaults already point at Ollama + qwen2:7b
python chat.py --input samples/p_alt_id_tb.csv
> validate p_alt_id_tb
> mask the SSNs in p_alt_id_tb
> mask everything          # deliberately ambiguous — watch it ask back instead of guessing
```

Without Ollama running, `chat.py` still starts and every request will
correctly come back as a clarifying question ("I couldn't reach the
ollama model...") rather than crashing or guessing — that's the graceful-
failure path `OllamaProvider` is tested against.

## Try the Phase 4 multi-agent fan-out (from Python — no CLI yet)

```python
from pathlib import Path
from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory
from core.audit import AuditLog
from core.llm_provider import load_default_provider

manager = ManagerAgent(
    llm=load_default_provider(),
    config_dir=Path("config"), ddl_dir=Path("input/ddl"),
    plan_memory=PlanMemory(Path("output/plans")),
    audit_log=AuditLog(Path("output/logs/audit.jsonl")),
)

plan = manager.plan("validate p_dtl_tb and p_alt_id_tb together")  # 2+ tables -> fan-out
csv_paths = {"p_dtl_tb": Path("samples/p_dtl_tb.csv"), "p_alt_id_tb": Path("samples/p_alt_id_tb.csv")}
preview = manager.run_fan_out_preview(plan, csv_paths, mask=True)
manager.request_human_confirmation(plan, ask=lambda msg: input(msg + "\ny/N: ").lower() == "y")
result = manager.execute_fan_out(plan, csv_paths, policies=preview["_policies"], output_dir=Path("output/sql"))
print(manager.report(plan.plan_id))
```

A one-table request through `manager.plan()` still takes the unchanged
Phase 3 linear path (`preview()`/`execute()`) — fan-out only triggers for
genuinely multi-table requests (Section 2.3), and even then only after the
Manager validates every named table actually exists.

## Try it against a real Postgres instance

```bash
cp .env.example .env   # fill in DB_HOST/DB_USER/DB_PASSWORD/...
python converter.py    # unchanged: convert + create schema + load sample data
```

Then, from Python:

```python
from pathlib import Path
from adapters.postgres_adapter import PostgresAdapter
from config import DB_CONFIG, PG_SCHEMA

pg = PostgresAdapter(DB_CONFIG, schema=PG_SCHEMA)
pg.connect()
tables = pg.introspect_schema(PG_SCHEMA)
print(f"{len(tables)} tables introspected from live Postgres")
```

## What's deliberately NOT here yet

- SQL Editor now supports optional Monaco integration in browser when the
  frontend packages are installed; it still falls back to a lightweight
  textarea preview for demo and offline mode. The new Lineage screen ships
  with an SSR-safe client-side React Flow integration when available, and a
  fallback SVG visualization for build/SSR verification. (API Explorer is
  done — a link to FastAPI's own `/docs`. DBA Console's multi-*table* bulk
  masking overview is done, ADR-0013 — its multi-*environment* dimension is
  not: this project has one target database, not several to batch across.)
- No interaction-level (click/event) test coverage for the UI — this
  project's verification is bundle-compile + SSR render (catches crashes/
  markup errors) plus one `jsdom` interactive test (ADR-0011). Most
  click/checkbox/form interactions, including the Approvals bulk-select
  UI (ADR-0012), have no automated test beyond "it renders."
- Real Keycloak/OIDC integration — `core/auth.py`'s `LocalDevAuthProvider`
  closes "anyone can call any endpoint" and "no way to manage roles," it
  is not enterprise SSO. No MFA, self-service password reset, or account
  lockout either.
- CORS is configurable and its lockdown status is observable
  (`GET /api/health`), but the default is still wide open until
  `CORS_ALLOWED_ORIGINS` is actually set — see `backend/app.py`
- WebSocket/SSE streaming for live job progress — Job Monitor polls
  `GET /api/jobs` every 4s instead
- Real format-preserving *encryption* (Phase 2 ships a deterministic,
  non-reversible-without-salt substitution — see ADR-0002's limitation note)
- UPDATE/UPSERT/DELETE SQL generation (Phase 4 ships INSERT only)
- Parallel subagent dispatch — fan-out jobs dispatch sequentially per table
- Live Oracle connectivity, MySQL/SQL Server adapters
- Live-database integration tests for `ExecutionReportAgent` (covered via
  `FakeDatabasePort`; a real Postgres/Oracle run is a documented follow-up)
- One remaining sample-data gap: a column's real meaning that depends on
  a *sibling* column's value (e.g. `p_alt_id` — SSN, NPI, or DEA number,
  depending on `p_alt_id_ty_cd`) can't be inferred by
  `backend/sample_rows.py`'s one-column-at-a-time heuristic — narrower
  than the word-salad problem ADR-0012 fixed, but still open

Next checkpoint per the spec's phase gate: confirm before adding the
remaining Phase 5/6 screens, actually setting `CORS_ALLOWED_ORIGINS` for a
real deployment, adding interaction-level UI tests, or a real Keycloak
integration — whichever matters most for the next real use case.
