# ADR-0010: Approval Dashboard + API Explorer

**Status:** Accepted
**Scope:** a real functional gap, discovered before being built around:
Agent Console only ever showed the single plan a browser session had just
created, held in local React state — a plan sitting in
`awaiting_confirmation` from any other source (a different session, a
colleague, a page refresh) was invisible and unreachable through the UI
even though the backend fully supported acting on it by `plan_id`. Also
closes the "API Explorer" item from the spec's screen list — for free,
since FastAPI already serves one.

## Why this, specifically, this pass

Every screen built so far in Phase 5 has mapped cleanly onto a spec
checklist item. Before adding the next one by default, it was worth
checking whether the checklist was actually complete for what's already
built — and it wasn't: `Job Monitor`'s row-detail chevron
(`<ChevronRight>`) was never wired to anything, which on inspection meant
there was no way to act on a pending plan except immediately after
creating it in that same session. That's a real usability hole in a
product whose entire premise is "propose, then a human confirms" — worth
fixing before adding more surface, same reasoning as every prior
"foundational gap before more screens" decision in this project.

## Decision

- **`ApprovalDashboard`** (`ui/console.jsx`) — lists every plan in
  `awaiting_confirmation` (via the existing `GET /api/jobs`, filtered
  client-side), lets an ADMIN open one (`GET /api/agent/{id}`, showing the
  real preview/validation text) and approve-and-execute or reject it
  (`POST .../confirm` then `POST .../execute`, or just `confirm` with
  `confirmed: false`) — the exact same endpoints Agent Console already
  used, no backend changes needed. Dual-mode like every other screen:
  live data, or a demo mock list when there's no backend to talk to.
- **API Explorer**: not rebuilt. FastAPI serves a full interactive
  Swagger UI at `/docs` by default (confirmed: `docs_url` was never
  disabled in `backend/app.py`) — the sidebar just gained an external
  link to it, shown only when `apiStatus === "live"`, since there's
  nothing to explore without a reachable backend. Building a second,
  custom API explorer when a complete one already exists for free would
  have been effort spent on a checkbox rather than a gap.

## Verification

The claim this screen exists to prove — that a DIFFERENT user's session
can discover and act on a plan they didn't create — was tested directly
rather than assumed from "the endpoints already worked in isolation":
`tests/test_phase5d_approvals.py` logs in as `operator`, creates and
previews a plan, then logs in *separately* as `admin` (a different
`TestClient` session, no shared state except what the backend persists),
discovers the plan purely through `GET /api/jobs`, fetches its detail
purely by `plan_id`, and confirms + executes it — then confirms it drops
out of the pending list once completed. A second test confirms the
ADR-0008 ADMIN-only gate holds even for an operator trying to approve
their *own* plan through this same path — the gate isn't specific to
Agent Console's code path, it's enforced at the endpoint regardless of
which screen calls it.

An earlier attempt to verify this against a real `uvicorn` process with
raw `curl` was inconclusive — the shell session hit a timeout mid-script,
and separately, a real (unreachable) Ollama correctly returned
`intent: "unclear"` for the test request, which is graceful, expected
behavior (ADR-0003) but not useful for testing the approval *path*
specifically. Rather than fight the tooling or force a misleading
"success," the verification was moved to where this project has always
gotten its most reliable signal: a `FakeLLMProvider`-backed `TestClient`
test that exercises the real cross-session logic deterministically. Noted
here because it's a small example of the same principle behind
everything else in these ADRs — say what actually happened, including
when a verification attempt itself didn't pan out.

The usual bundle/SSR check also ran clean across all 7 screens (up from
6), including the new sidebar's external API Explorer link.

## Consequences

- An ADMIN can now actually run this platform as a triage workflow —
  "what's waiting on me" is one screen, not a thing you can only see if
  you happen to be the person who typed the request.
- `Job Monitor`'s chevron is still decorative for completed/failed/running
  jobs (only pending ones are actionable, which is the only state where
  "act on this" means anything) — not extended to a full job-detail view
  this pass.

## What was intentionally NOT done this pass

- Bulk approve/reject (approving N pending plans at once) — Section 7a's
  DBA Console territory, deferred with the rest of that screen.
- Push notifications / badge counts for pending approvals — the sidebar
  doesn't currently show "3 pending" anywhere; a user has to open the
  screen to find out.
- Rich diff rendering for multi-table (fan-out) plan previews in the
  Approval Dashboard — it shows whatever `preview.validation_text` (or the
  raw preview JSON as a fallback) contains, which is complete but not as
  polished as Agent Console's per-table breakdown for fan-out jobs.
