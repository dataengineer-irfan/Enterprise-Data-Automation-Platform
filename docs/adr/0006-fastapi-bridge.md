# ADR-0006: FastAPI Bridge (Phase 5, backend half)

**Status:** Accepted
**Scope:** closes the gap ADR-0005 flagged as the most important one —
`ui/console.jsx` had no backend to talk to. This ADR is that backend:
`backend/app.py` and five routers mapping HTTP onto Phases 1–4's Python
classes one-for-one. The UI itself was **not** rewired to call this API in
this pass — its mock data and `setTimeout`-simulated workflow are
untouched; see "What this is NOT" below.

## Why this before more UI screens

Asked which was the better next move — more Phase 5 screens, or the
backend bridge ADR-0005 flagged — the backend was the clear answer: every
additional screen built against mock data would only have compounded the
same gap. This closes it structurally instead.

## Decision

- **`backend/app.py`** — one `FastAPI` app, five routers
  (`schema`, `masking`, `agent`, `jobs`, `audit`), CORS wide open with an
  explicit "dev-only, tighten before deploying anywhere" comment rather
  than a silent security gap.
- **`backend/dependencies.py`** — one process, one `ManagerAgent`
  singleton (lazily constructed, so importing this module never requires
  a live Ollama). `set_manager()` / `reset_for_tests()` let tests inject a
  `FakeLLMProvider`-backed manager, the same pattern every prior phase's
  tests use (ADR-0003).
- **HTTP verbs map onto the six-step workflow one-for-one**:
  `POST /api/agent/plan`, `POST /{id}/preview`, `POST /{id}/confirm`,
  `POST /{id}/execute`, `GET /{id}/report` — five separate requests, not
  one "do everything" endpoint, because each step in the real workflow is
  independently audited (rule #1/#3); collapsing them into one call would
  misrepresent what the backend actually enforces.
- **`backend/sample_rows.py`** generates plausible rows for any table on
  demand (name/type-aware, seeded Faker) rather than requiring a
  hand-written CSV per table — the Masking Designer and Agent Console
  screens work against every table in the schema, not just the one
  (`p_alt_id_tb`) with a Phase 2 CLI sample file.
- **Dev-mode, explicitly flagged shortcut**: `preview` and `execute` are
  separate HTTP requests, but `ManagerAgent.preview()`/`execute()` (Phase
  3) take a CSV path and expect to be called against the same rows both
  times. Since HTTP is stateless, rows and proposed masking policies are
  cached in-process by plan ID between the two calls
  (`_pending_rows`/`_pending_policies` in `dependencies.py`). This is
  correct for a single-process dev/demo deployment and explicitly wrong
  for a multi-worker one — flagged in both the module docstring and here,
  not discovered later as a surprise.

## Two real bugs found via testing, fixed at the source (not patched around)

1. **Plan-memory singleton mismatch.** The first version of
   `dependencies.py` constructed its own module-level `_plan_memory`
   instance at import time, separate from whatever `PlanMemory` an
   injected (test) `ManagerAgent` was actually using. Every
   plan-lookup-after-creation test failed with 404s that read like routing
   bugs but were really two different storage directories being written
   to and read from. Fixed by deriving `get_plan_memory()`/`get_audit_log()`
   from the *current* manager instance (`agent/manager.py` gained
   `plan_memory`/`audit_log` properties for this) instead of keeping a
   parallel singleton — one source of truth, structurally, not by
   special-casing the test path.
2. **Inconsistent table-name casing across one endpoint.** `GET
   /api/schema/tables/{name}` combines two subagent dispatches
   (`introspect_schema`, which returns lower_case column/table names from
   the DDL-converted metadata, and `build_fk_graph`, which returns
   `relationships_verified.yaml`'s native `UPPER_CASE` table names). The
   endpoint was returning both conventions mixed in one response — a
   caller-hostile API contract, not just a test assumption being wrong.
   Fixed by normalizing `parents`/`children` to lower_case before
   returning, so every identifier this API exposes follows one convention.

Both were caught the same way every bug in this project has been caught:
write the test for the real end-to-end behavior, not just "does this
function return without throwing," and take a failing test as a
root-cause prompt rather than a reason to loosen the assertion.

## Verification

16 API tests (`tests/test_phase5_api.py`) using FastAPI's `TestClient` —
full single-table and fan-out workflow walks over HTTP, the 403 on
unconfirmed execution, ambiguous/unknown-table clarifying-question paths,
masking propose/override/preview, jobs and audit endpoints including
secret redaction. Beyond the in-process `TestClient` tests, the server was
also actually booted with `uvicorn` and hit with real `curl` requests
against a genuinely unreachable Ollama, confirming the graceful-failure
path works outside the test harness too, not just inside it — see the
transcript in this session for the real response: a clean `unclear` plan
with a clarifying question naming the exact `ollama pull` command needed,
not a 500 or a hang.

## Public-API cleanup made in passing

While wiring the routers, several places reached into `ManagerAgent`'s
underscore-prefixed internals (`_tables`, `_storage`,
`_order_tables_for_execution`). Rather than let the API layer normalize
that as acceptable, `ManagerAgent` gained proper public accessors
(`get_table`, `list_table_names`, `storage`, `plan_memory`, `audit_log`,
`order_tables_for_execution`) and every router was updated to use them.
Small, but worth recording: an API layer is exactly the kind of consumer
that makes a class's real public surface honest.

## What this is NOT

- **The UI is not wired to this API yet.** `ui/console.jsx` still uses its
  own mock data and `setTimeout` simulation. Pointing it at this backend
  (base URL config, replacing mock renders with `fetch`/loading states,
  handling the real `clarifying_question` / 403 / 404 paths this API
  actually returns) is the next concrete step, not done here.
- No auth on any endpoint (same open Keycloak gap as every prior phase).
- No WebSocket/SSE streaming for `stream_progress` — Job Monitor would
  need to poll `GET /api/jobs` today; a real live-progress feed is a
  follow-up.
- No pagination on `/api/audit` beyond a `limit` query param — fine for a
  dev audit log, not for a production-scale one.
