# ADR-0011: Session-Expiry Handling + Pending-Approval Badge

**Status:** Accepted
**Scope:** closes the last two items on the README's own "small, real gaps"
list from ADR-0010: a JWT expiring mid-session used to surface as a
generic per-screen error banner rather than returning the user to the
login gate, and the sidebar had no indication of how many plans were
actually waiting on approval anywhere without opening that screen.

## Session-expiry handling

**Problem**: `apiGet`/`apiPost` already threw a structured error on any
non-2xx response, and every screen already caught it and showed a local
error banner. But a 401 specifically means "the session itself is no
longer valid" — no amount of retrying or local error display fixes that;
the only correct response is to return to the login gate. Nothing did
that automatically.

**Decision**: rather than thread a callback through the ~20 existing
`apiGet`/`apiPost` call sites across 7 screens, `apiGet`/`apiPost` report
a 401 that came with a token (never on `/api/auth/login` itself, or on
the unauthenticated `/api/health` check, where a 401/absence means
something different) to a single module-level handler the shell registers
on mount: `setSessionExpiredHandler(fn)`. The shell's registered handler
clears `authToken`/`authUser` and sets a specific `authError` message
("Your session expired — please sign in again"), which `LoginGate`
already had a prop for. This is additive — every screen's own local error
handling is untouched; the session-level reaction happens in parallel to
whatever the calling screen already does with the same thrown error.

**Verification**: SSR alone can't test this — it never runs `useEffect`,
so the poll that actually surfaces a 401 in practice would never fire, and
the fix is a runtime state-transition, not markup. Instead of taking the
static logic on faith, this pass installed `jsdom` and drove a real
`react-dom/client` root through an actual login (mocked `fetch`, real
DOM, real event dispatch) followed by a mocked 401 on the pending-approval
poll, and confirmed the session-expired message appeared and the app
genuinely returned to the login screen.

The first version of that test was **wrong and would have "passed" for
the wrong reason**: it set `input.value = "..."` directly and dispatched a
plain event, which doesn't trigger React's controlled-input change
detection (React tracks input value changes via a wrapped native setter,
not the DOM's own `value` property — a well-known gotcha absent
`@testing-library/react`'s helpers). That test reported `fetch` was never
even called and "still on Sign in" as if that were a pass — it was
actually a silent no-op, not a passing assertion. Caught by checking the
call counts the test itself logged (`fetch calls made: 0`) rather than
trusting the boolean output, and fixed by setting the value through the
native `HTMLInputElement.prototype.value` setter before dispatching the
`input` event, which is what the framework actually watches for. Recorded
here per this project's running practice: say what a verification attempt
actually did, including when the first attempt was hollow.

## Pending-approval badge

Small, but genuinely useful: the sidebar's "Approvals" nav item now shows
a live count, polling the exact same `GET /api/jobs` endpoint and the
exact same `status === "awaiting_confirmation"` filter the Approval
Dashboard screen itself uses (ADR-0010) — computed once, in the shell, so
the badge and the screen can never disagree about what "pending" means. A
transient poll failure leaves the badge at its last-known value rather
than flashing to zero, since a momentary network hiccup shouldn't look
like "nothing's pending" when something plausibly still is.

## Consequences

- A user working through an expired session gets a clear, specific reason
  and a way forward, rather than a screen that quietly stops working with
  a generic "HTTP 401" banner they have to interpret themselves.
- The sidebar is now the first thing that tells an ADMIN whether anything
  needs their attention, rather than requiring them to open the Approvals
  screen speculatively.
- This is the second time in this project's history that a test appeared
  to pass while testing nothing (see also: the earlier live-`curl` script
  that got interrupted mid-run and was replaced with a deterministic test,
  ADR-0010) — worth noting as a pattern to stay alert for, not just a
  one-off mistake.

## What was intentionally NOT done this pass

- No refresh-token flow — the session still just ends at expiry; this
  ADR makes that ending graceful, it doesn't extend the session.
- The pending-approval poll interval (5s) is a flat constant, not
  configurable, and not backed off when the tab is backgrounded.
- No equivalent "count" badge for any other screen (e.g. failed jobs on
  Job Monitor) — only Approvals got one, since it's the one screen where
  "how many need me" is the entire point of opening it.
