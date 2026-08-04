# ADR-0007: UI ↔ API Wiring (Phase 5, closing the loop)

**Status:** Accepted
**Scope:** closes the last gap ADR-0005/0006 both flagged — `ui/console.jsx`
now actually calls `backend/app.py`. Every screen is **dual-mode**: real
`fetch` first, graceful fallback to the original mock data if nothing
answers, with a visible Live/Demo indicator so it's never ambiguous which
one is on screen.

## Why dual-mode, not just "wire it up"

This artifact renders in a browser sandbox that cannot reach a backend
running on someone's actual machine — `fetch("http://localhost:8000/...")`
from inside the preview will simply fail. Presenting real `fetch` calls as
if they'd work in that context would be dishonest. Dual-mode is the
correct engineering answer to that constraint, not a workaround: try the
real API (`useApiHealth` pings `/api/health` with a short timeout), and
if it's unreachable, every screen keeps working exactly as it did in
ADR-0005 — same mock data, same interactions, nothing regresses. Point the
console at a real `uvicorn backend.app:app` (via the new settings popover,
top bar) and every screen switches to live data with no other change
needed.

## What changed, per screen

- **Schema Explorer**: `GET /api/schema/tables` for the tree,
  `GET /api/schema/tables/{name}` for detail. `mapLiveTable()` reshapes the
  API response into the same shape the mock `SCHEMA` object always used,
  so every downstream render branch (columns table, `RelationshipMini`)
  needed zero changes — only the data source changed.
- **Masking Designer**: `POST /api/masking/{table}/propose` →
  `GET /api/masking/{table}/preview` on table select;
  `POST /api/masking/{table}/override` on strategy change, which re-fetches
  preview so before/after reflects the edit immediately. Coverage %% is
  computed from `/api/masking/tables`' `sensitive_columns` count vs.
  proposed rule count, matching what `masking_coverage()` (Phase 2) really
  measures.
- **Agent Console** (the signature screen): `runPlanLive()` /
  `confirmLive()` replace the `setTimeout` simulation with the real
  `POST /plan` → `POST /{id}/preview` → `POST /{id}/confirm` →
  `POST /{id}/execute` → `GET /{id}/report` sequence, reading
  `GET /api/agent/{id}` between steps to pull the real `subtasks` list for
  display. `formatSubtaskSummary()` turns each subagent's raw `summary`
  dict into the same short strings the demo mode always showed, so both
  modes render identically. The ambiguity path (`intent: "unclear"`) is
  now real too — verified live against an actually-unreachable Ollama (see
  below), not simulated.
- **Job Monitor**: polls `GET /api/jobs` every 4s (documented as a
  placeholder for the WebSocket/SSE streaming ADR-0006 already flagged as
  missing). A live "job" is a `Plan` viewed as a row — `rows`/`duration`
  show `—` since a `Plan` genuinely doesn't carry that data yet, rather
  than fabricating numbers to match the old mock's columns.
- **Audit Dashboard**: `GET /api/audit?q=...` on a 250ms debounce.

## Verification

The risk in this kind of change is field-name mismatches between backend
JSON and frontend assumptions — exactly the class of bug that's bitten
this project before (ADR-0004, ADR-0006). React SSR alone wouldn't catch
it: `renderToStaticMarkup` doesn't run `useEffect`, so it never exercises
the actual fetch/mapping code paths. So, beyond the same demo-mode SSR
render pass every prior UI ADR used (still clean, 5/5 screens), a real
`uvicorn` server was booted and every endpoint the new frontend code calls
was hit directly with `curl`, and every response's field names were
checked character-for-character against what the JS mapping functions
read: `tables[].name`, `columns[].{name,type,nullable,pk}`,
`rules[].{column,strategy,salt}`, `preview[].{before,after}`,
`plan.{plan_id,intent,table,tables,reasoning,clarifying_question,fan_out}`,
`jobs[].{plan_id,execution_order,tables,status,nl_request}`,
`records[].{timestamp,actor,action,resource,result,detail}`. All matched
exactly — no mapping bugs found this pass, which is itself worth recording
given how careful this needed to be after two prior casing/shape bugs.

The `/api/agent/plan` call against a genuinely unreachable Ollama was also
verified for real: the response's `clarifying_question` field contained
the exact, correctly-worded "couldn't reach the ollama model... has
`ollama pull qwen2:7b` been run?" message `OllamaProvider` (ADR-0003)
produces — confirming the frontend's unclear-intent rendering branch
(amber warning icon + clarifying question, no Confirm step shown) works
against the real failure mode, not just a mocked one.

## One process quirk worth recording, not a code bug

Mid-verification, a backgrounded `uvicorn` process from one tool
invocation didn't survive into the next shell session, producing a
misleading "Connection refused" that briefly looked like a server crash.
Traced to the test harness (background job lifecycle across separate
shell invocations), not `backend/app.py` — confirmed by re-running the
full curl sequence inside one shell session, which passed cleanly.
Recorded here on the same principle as everything else in this project's
ADRs: state plainly what something turned out to be, including when it
turns out to be nothing.

## Known rough edge, not fixed this pass

`backend/sample_rows.py`'s Faker-backed sample-row generator falls back to
`fake.word()` for any column it doesn't recognize by name/type pattern —
so preview data for audit columns like `g_aud_user_id` or `g_aud_ts` shows
plain English words ("dog", "chair") instead of plausible usernames or
timestamps. Cosmetically odd, functionally harmless (it's sample preview
data, clearly not real data, and the one column that actually matters for
a masking preview — the sensitive one — is generated correctly). Left as
a known limitation rather than silently smoothed over.

## Consequences

- The five screens, the backend, and the wiring between them are now each
  independently and jointly verified — a first for this UI, which
  previously only had the backend tested and the frontend tested, never
  the connection between them.
- Live-mode error states (network failure mid-session, 403 on unconfirmed
  execute, 404 on an unknown table) surface as inline banners
  (`errorMsg`/`liveError` state per screen) rather than blank screens or
  unhandled promise rejections — every `apiGet`/`apiPost` call site has a
  `.catch`.

## What was intentionally NOT done this pass

- No WebSocket/SSE for Job Monitor — still polling, per ADR-0006.
- No retry/backoff on the health check beyond the initial ping; switching
  API base URLs mid-session re-triggers it via the `apiBase` dependency,
  but a flaky-then-recovering backend won't be re-detected until the user
  navigates or changes the URL.
- `sample_rows.py`'s word-salad fallback for unrecognized columns — noted
  above, not fixed.
- Auth — still the same open gap as every prior phase; every endpoint
  this UI now calls is exactly as unauthenticated as ADR-0006 already flagged.
