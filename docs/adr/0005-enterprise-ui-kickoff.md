# ADR-0005: Enterprise UI Kickoff (Phase 5, screens 1–5 of the spec's list)

**Status:** Accepted, partial — this is a **standalone interactive
front-end**, not yet wired to the Phase 1–4 Python backend. That gap is
the single most important thing in this ADR; see "What this is NOT" below
before treating anything here as production-connected.

## Scope delivered

`ui/console.jsx` — one React artifact covering the five screens the spec
prioritizes first for Phase 5: Schema Explorer, Masking (Rule) Designer,
AI Chat Panel (built here as an "Agent Console"), plus Job Monitor and
Audit Dashboard pulled forward from the DBA Console section since they
share the same data shapes already modeled in `agent/plan_memory.py` and
`core/audit.py`.

## Design system (per the frontend-design skill's plan-then-critique process)

**Subject grounding**: this isn't a generic admin panel — it's a console
for a governed, audited, multi-agent data platform sitting on a real
Medicaid MMIS schema. The design had to earn that seriousness rather than
borrow a generic SaaS dashboard look.

**Palette** (6 named hex): `#0A0D13` ink base / `#11151E` surface /
`#171C27` raised surface / `#232A38` hairline border, plus three semantic
accents used with strict discipline: `#14E0C2` teal = **data state**
(validated, live, FK-linked), `#8B7CF6` violet = **agent state** (Manager/
subagent activity only — never used for data), `#F5A524` amber = needs
review, `#FB4B67` rose = blocked/error. The teal/violet split is not
decorative — it mirrors a real architectural distinction from ADR-0003/4
(deterministic data-layer code vs. the LLM-driven planning layer), so a
user can learn "violet = the agent is doing something" as a real signal.
Explicitly avoided the two AI-generated-design tells flagged in the skill:
no warm-cream-plus-terracotta, no near-black-plus-single-neon-accent
minimalism — this uses three disciplined accents with distinct jobs, not one.

**Type**: Space Grotesk (display — geometric, technical personality, used
only for headings/nav, never body copy) + Inter (body) + JetBrains Mono
(table names, column names, SQL, audit actor/action fields) — the
monospace choice is functional, not aesthetic: this is a schema tool, and
column names in a proportional font would be actively harder to scan.

**Signature element**: the Plan→Explain→Preview→Confirm→Execute→Report
pipeline rendered as a live vertical stepper with a connecting line and
per-step detail (dispatched subagents, confirm/reject buttons, final
report). This was chosen deliberately over a generic chat-bubble UI
because a chatbot look would *hide* the actual safety architecture this
product is built around; the stepper makes it visible and legible instead.
The self-critique pass: numbered step markers are usually a generic
default (flagged explicitly in the skill), but this is the one case where
they're correct — the six steps are a real, code-enforced sequence
(ADR-0003's rule #1), not decoration.

**Interaction details built to the spec's floor**: `Cmd/Ctrl+K` command
palette (jump to any screen or table), light/dark toggle (two full token
sets, not just an inverted filter), visible `:focus-visible` rings on
every interactive element, `prefers-reduced-motion` respected globally,
responsive grid (12-column, collapses reasonably at narrower widths).

## Verification

No visual browser tool was available, so correctness was checked the way
this codebase has checked everything else — programmatically: `esbuild`
bundle-compiles the file cleanly, and `react-dom/server`'s
`renderToStaticMarkup` was used to actually render all 5 screens plus 4
schema-explorer edge cases (a root table with 0 parents, a leaf table with
0 children, `p_affl_tb`'s self-referencing double-FK to `p_dtl_tb`, and a
composite-PK owner table) — every one rendered without a thrown error.
This catches real runtime bugs (undefined props, bad array access) that a
syntax-only check would miss; it does not substitute for an actual visual
review, which is a documented follow-up.

## What this is NOT (read before wiring anything to this)

- **Not connected to the Python backend.** There is currently no HTTP API
  layer — `ManagerAgent`, `ValidationEngine`, `MaskingEngine`, etc. are
  plain Python classes (Phases 1–4), and this UI is plain client-side
  React with no fetch calls. Every table, column, masking preview, agent
  dispatch, job, and audit row in this file is **realistic mock data**
  (grounded in the real schema names/FKs/sensitive flags this project
  actually built) or a `setTimeout`-simulated version of the real
  Plan→Explain→Preview→Confirm→Execute→Report flow — not a live call
  into `agent/manager.py`. Building a FastAPI (or similar) layer that
  exposes `ManagerAgent` over HTTP/WebSocket, and swapping this file's
  mock data for real `fetch`/`EventSource` calls, is the necessary next
  step before this is a real product surface, not just a demo of one.
- **Not using Monaco** for a SQL editor screen (Section 7's "SQL Editor
  (Monaco)") — not built this pass.
- **Not a full D3 lineage graph** — Schema Explorer's relationship panel
  is a lightweight custom SVG parent/child list, not a force-directed
  graph. Sufficient for a single table's immediate neighbors; a full
  multi-hop lineage view is a real D3 (or react-flow) build, deferred.
- **Not RBAC-aware** — no User/Role Management screen, no permission
  gating on any action in this file (every button is clickable regardless
  of "who" is using it, since there's no auth layer yet — see Phase 1's
  Keycloak gap, still open).
- **Not the full DBA Console** (Section 7a's batch cross-environment
  actions, masking-coverage-% fleet view, dry-run toggles at scale) — Job
  Monitor and Audit Dashboard here are single-environment, single-table-job
  scoped, matching what Phase 4's backend actually supports today.

## Consequences

- Every screen is grounded in this project's real schema and architecture
  rather than generic placeholder content, so it's usable immediately as a
  design reference / stakeholder demo, and the component boundaries
  (`SchemaExplorer`, `MaskingDesigner`, `AgentConsole`, `JobMonitor`,
  `AuditDashboard`, each taking a `t` token object as a prop) map cleanly
  onto where real data-fetching hooks will go once a backend API exists.
- The teal/violet semantic split and the stepper signature element are
  now the established visual language — any future screen (SQL Editor,
  DBA Console, Admin) should extend this system rather than introduce a
  new one.

## What was intentionally NOT built this pass

- The FastAPI/HTTP backend layer connecting this UI to Phases 1–4 (the
  most important gap — see above)
- SQL Editor (Monaco), full lineage graph (D3/react-flow), Admin/RBAC
  screens, full DBA Console — remaining Phase 5/6 screens
- Automated visual regression testing (no browser tool available in this
  environment); render-correctness was verified via SSR, not pixel review
