# ADR-0008: Auth / RBAC Layer

**Status:** Accepted
**Scope:** closes the single most-repeated flagged gap across ADR-0001,
0005, 0006, and 0007 — "every endpoint callable by anyone who can reach
it." Every route except `/api/health` and `/api/auth/login` now requires
a valid bearer token, and the write-authorizing actions (agent
confirm/execute, masking propose/override) require specific roles, not
just "any authenticated user."

## Why this, not more screens

Every prior UI/API ADR flagged the same open risk, in the same words:
"CORS is wide open," "no auth on any endpoint," "every endpoint callable
by anyone." Given the choice of what to build next, closing a repeatedly-
flagged foundational gap took priority over adding more UI surface area on
top of it — the same reasoning that drove "backend before more screens"
(ADR-0006) and "wiring before more screens" (ADR-0007).

## An honest note on how this was built

Partway into this pass, files appeared in the project that hadn't been
written in this session: `core/auth.py`, `backend/auth_deps.py`,
`core/actor_context.py`, plus already-updated versions of
`agent/manager.py`, `agent/subagents/execution_report_agent.py`,
`backend/routers/schema.py`, `backend/routers/masking.py`,
`backend/routers/jobs.py`, `backend/routers/audit.py`, `backend/app.py`,
and a `config/users.yaml` with a different (and better) schema than the
one being drafted here — plus two complete test files
(`tests/test_phase5_api.py`'s auth-aware update and
`tests/test_phase5b_auth.py`). This is a shared, persistent environment;
that work was already done, mid-flight, before this pass started typing.

Rather than silently claim sole authorship or blindly overwrite it, the
existing implementation was inspected in full before anything further was
written. It turned out to be a better design than the one in progress —
notably a `contextvars`-based `core/actor_context.py` that solves a real
concurrency bug (a fixed constructor-time `actor` string on `ManagerAgent`
would misattribute audit records under concurrent requests from different
users; per-request context-local actor tracking fixes this properly) that
hadn't even been considered yet. The response was to **adopt it**: the two
draft files written before this was discovered (`core/auth_provider.py`,
`core/jwt_utils.py`) were deleted, `config/users.yaml` was restored to its
original (accidentally clobbered mid-investigation, then verified
byte-for-byte reconstructable via its own hash algorithm and restored)
schema, and only the one file actually missing — `backend/routers/agent.py`'s
auth wiring — was completed, matching the existing pattern exactly rather
than introducing a second, inconsistent auth scheme.

This is worth recording plainly, per this project's own running practice
of stating what something turned out to be rather than smoothing it over.

## Design (as adopted)

- **`core/auth.py`** — `Role` as an ordered enum (`VIEWER < OPERATOR <
  ADMIN`), `User` dataclass, `LocalDevAuthProvider` loading
  `config/users.yaml` (HMAC-SHA256 password hashing, JWT issuance built
  in). Explicitly a dev-mode local provider, not Keycloak/OIDC — the
  interface is positioned so a real Keycloak adapter is a contained
  addition later, matching the `DatabasePort`/`LLMProvider` pattern.
- **`core/actor_context.py`** — `contextvars`-based per-request actor
  tracking. `ManagerAgent` and `ExecutionReportAgent` no longer attribute
  every audit record to a fixed constructor-time string; they read the
  actual logged-in user for *this* request out of context-local state.
  Confirmed by test (`test_different_requests_get_correctly_isolated_actors`)
  that back-to-back requests from different users don't leak actor
  identity between them — a real regression guard, not a hypothetical one.
- **`backend/auth_deps.py`** — `with_actor` (any authenticated user) and
  `require_role(Role)` (minimum-role gate) as FastAPI dependencies.
  Missing/garbage tokens → 401; insufficient role → 403 with a message
  naming the required role, confirmed against a live server:
  `{"detail": "Requires OPERATOR+ role; viewer has VIEWER."}`.
- **Role boundaries, deliberately stricter than "any operator can do
  anything a viewer can't"**:
  - `VIEWER`: read schema, masking preview, jobs, **not** audit (the audit
    trail names other users' actions — judged more sensitive than schema
    metadata or job status, a deliberate departure from the "any reader"
    default the other read endpoints use).
  - `OPERATOR`: + plan/preview jobs, propose/override masking rules,
    read audit.
  - `ADMIN`: + **confirm and execute** — the actual write-authorizing
    actions. This is stricter than the Phase 4 design's original
    assumption (that any operator-level confirmation was enough); rule
    #2's "only a CONFIRMED plan may write" now also means "only an ADMIN
    may confirm it."
- **`config/users.yaml`** — three dev seed accounts (`viewer`/`operator`/
  `admin`, passwords `<username>-dev-pw`), documented in-file as
  throwaway dev credentials, exactly like the CORS-wide-open and
  default-JWT-secret notes elsewhere in this project.

## UI wiring (this pass's own work)

- `ui/console.jsx` gained a `LoginGate` screen, shown whenever the API is
  reachable (`apiStatus === "live"`) but no session token exists yet —
  demo mode never shows it, since there's nothing to authenticate against
  without a real backend.
- Every `apiGet`/`apiPost` call site across all five screens now threads
  an `apiToken` parameter, sent as `Authorization: Bearer <token>`.
- The Confirm/Execute buttons (Agent Console) and the masking-strategy
  override select (Masking Designer) are disabled with an inline message
  when the logged-in user's role is insufficient — visibly matching the
  backend's actual enforcement rather than only failing silently at
  request time.
- Top bar shows the real logged-in display name + role, with a
  click-to-sign-out control.

## Verification

Beyond the adopted test suite (`test_phase5b_auth.py`'s 12 tests +
`test_phase5_api.py`'s auth-aware rewrite, both already passing once
`backend/routers/agent.py` was completed — 85/85 total), the UI wiring was
checked the same way ADR-0007 checked the rest: `esbuild` bundle-compiled
cleanly, `react-dom/server` rendered every screen (including forcing the
`LoginGate` branch specifically, confirmed to show the "Sign in" heading
and dev-seed-account hint), and a real `uvicorn` server was hit with real
`curl` calls confirming the exact response shapes the UI depends on
(`{token, user: {username, role, display_name}}`) and the exact 403
message text the UI displays for an under-privileged action.

One real bug was found and fixed during the UI wiring itself, unrelated to
the adopted backend: inserting the `LoginGate` component definition via a
text replacement accidentally deleted the
`export default function EnterpriseConsole() {` line it was meant to
precede, leaving the shell's body orphaned outside any function — a
plain syntax error, caught immediately by the same `esbuild` bundle check
this project has used since ADR-0005, before it ever reached a render test.

## Consequences

- The three most-repeated words across every prior ADR — "wide open" —
  no longer describe the API surface. CORS itself remains open
  (a separate, still-flagged concern; see `backend/app.py`'s docstring)
  and there is still no real SSO/MFA/account-lockout/password-reset flow.
- Audit records now attribute real users, which makes the Audit Dashboard
  screen (ADR-0005/0007) actually meaningful for the first time — it was
  always structurally correct, but every actor was a fixed string until now.

## What was intentionally NOT done this pass

- Real Keycloak/OIDC integration — still explicitly deferred; `AuthProvider`
  is positioned for it, not a substitute for it.
- Refresh tokens, MFA, password reset, account lockout.
- CORS restriction — still wide open, still flagged, still separate from
  what this ADR closes.
- UI-side handling of a token expiring mid-session (the 1-hour JWT
  expiry from `core/auth.py`'s default `token_ttl_seconds` will eventually
  401 an in-progress session; today that surfaces as a generic error
  banner in whichever screen hit it, not an automatic return to the login
  gate) — a real, small, documented gap rather than a silently assumed
  non-issue.
