# ADR-0009: CORS Hardening + User Management

**Status:** Accepted
**Scope:** closes the two loops ADR-0008 left explicitly open — CORS was
still wide open, and RBAC existed with no way to manage it except
hand-editing `config/users.yaml` and regenerating a salted hash by hand.

## Why these two, together

Both are "close the gap, don't add surface" moves, the same reasoning
behind every prior "backend before more screens" / "wiring before more
screens" / "auth before more screens" decision. They're natural companions:
auth (ADR-0008) answered "who can call this," CORS answers "which origins
may call this from a browser," and User Management answers "who decides
who gets which role" — the three questions a real access-control story
needs answered together, not just the first one.

## CORS: from a docstring comment to an observable, configurable control

Previously: `allow_origins=["*"]`, with a comment explaining why that's
wrong for production. A comment nobody's forced to read isn't a fix.

Now:
- `CORS_ALLOWED_ORIGINS` (comma-separated) controls the actual allowlist.
  Unset → still defaults to `["*"]`, so the existing "just run uvicorn and
  point the UI at it" instructions keep working with zero setup — dual-mode
  reasoning again (ADR-0007): don't break the working default to fix the
  gap, make the gap impossible to overlook instead.
- **Loud at startup**: a real `warnings.warn(...)` fires when the default
  is in effect, not just a source comment — visible in any log aggregator,
  confirmed captured by pytest's own warning summary during this session's
  test run.
- **Observable at runtime**: `GET /api/health` now returns
  `cors_locked_down: bool`. A deployment can be verified locked-down with
  one curl call, not by reading source or trusting a comment. Confirmed
  against a live server (`{"status":"ok","cors_locked_down":false}` with
  no env var set) and by test
  (`test_health_reports_cors_locked_down_when_configured`, using
  `importlib.reload` to re-evaluate `backend/app.py`'s module-level CORS
  config under a monkeypatched env var — the one place in this codebase a
  test needed to reload a module rather than just construct a fresh
  instance, since the CORS origin list is computed once at import time by
  design, not per-request).

## User Management: closing the RBAC loop

- **`core/auth.py`**'s `LocalDevAuthProvider` gained real mutation
  methods — `list_users`, `create_user`, `set_role`, `delete_user` — each
  persisting back to `config/users.yaml` via a new `_save_users()` that
  preserves the file's other top-level keys (the dev-password convenience
  note) rather than clobbering them on write.
- **Safety guards, not just CRUD**: `set_role` and `delete_user` both
  refuse to demote/remove the last remaining ADMIN
  ("the platform would become unmanageable"), and the API layer separately
  refuses to let a user delete their own account while signed in as it.
  Two independent checks for the same failure mode (locking every admin
  out), the same "more than one layer for a rule that matters" pattern
  ADR-0004 used for write-authority.
- **`backend/routers/admin.py`** — `GET/POST /api/admin/users`,
  `PATCH/DELETE /api/admin/users/{username}`, all `require_role(Role.ADMIN)`.
  Explicitly gated to `LocalDevAuthProvider`: if the deployment is
  configured for `KeycloakAuthProvider`, every endpoint 400s with "manage
  users in your identity provider's own admin console instead" rather than
  silently no-op'ing or throwing an unhandled `AttributeError` — the same
  "flag it, don't hide it" instinct as everything else gated to a specific
  provider in this codebase.
- **UI**: a sixth screen, `UserManagement` — ADMIN-only (viewers/operators
  who navigate to it see a clear "ADMIN role required" panel, not a broken
  or empty one), with inline create/role-change/delete, matching the same
  dual-mode (live API / demo mock data) pattern every other screen uses.

## Verification

14 new tests (`tests/test_phase5c_admin_cors.py`): RBAC on every admin
endpoint, create → authenticate-as-the-new-user round trip, role
persistence across a fresh `LocalDevAuthProvider` instance (proving the
YAML write actually took), both last-admin guards, the can't-delete-self
guard, and a belt-and-suspenders check that the real dev `users.yaml` still
has exactly its original three accounts after the whole mutation-heavy test
module runs — every mutation test uses a temp-directory copy, verified
explicitly rather than assumed. All 99 project tests pass.

Beyond the test suite, a real `uvicorn` server was booted and hit with
real `curl` calls: confirmed `cors_locked_down` is observable and correct,
confirmed an admin can list/create users and a viewer correctly 403s on
the same call, and — the check that mattered most — confirmed the real
`config/users.yaml` on disk still had exactly its original three accounts
after the live run, proving the temp-copy discipline used throughout this
pass actually holds in a real process, not just under `TestClient`.

The UI addition was checked the same way every prior UI ADR has: `esbuild`
bundle-compiled cleanly, `react-dom/server` rendered all 6 screens
(including forcing `apiStatus="live"` with no token to confirm the Users
screen correctly falls through to the login gate like every other screen
does, rather than needing its own special case).

## Consequences

- An ADMIN can now run this platform end-to-end without ever hand-editing
  a YAML file or computing a password hash manually — the dev
  seed accounts in `config/users.yaml` are a starting point, not the
  permanent user list.
- CORS is still open by default; this ADR makes that fact impossible to
  miss rather than closing it outright, since closing it outright would
  mean picking an origin allowlist on this project's behalf that would
  almost certainly be wrong for whoever actually deploys it.

## What was intentionally NOT done this pass

- Self-service password change/reset (an ADMIN can change anyone's role,
  but there's no "let a user rotate their own password" flow — they'd need
  an admin to delete + recreate their account today).
- Bulk user import/export.
- Real Keycloak/OIDC — still explicitly deferred, same as ADR-0008.
- Automatic CORS origin detection from the UI's own `apiBase` setting —
  the allowlist is still a separate, manually-set env var on the backend,
  not something the UI can configure for you (and shouldn't be able to,
  since that would let a browser client widen its own permitted origins).
