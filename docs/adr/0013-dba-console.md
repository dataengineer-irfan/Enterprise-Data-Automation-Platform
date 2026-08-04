# ADR-0013: DBA Console (Bulk Masking Overview)

**Status:** Accepted
**Scope:** an 8th screen surfacing capability that was already fully
built and tested — `core.masking.MaskingPolicy.apply_bulk_pattern()` and
`masking_coverage()` shipped in Phase 2 (ADR-0002) but had no UI, and no
API endpoint even needed writing, since the per-table `propose`/`preview`
endpoints (ADR-0006) already did everything a bulk view needs — it just
had to call them for more than one table at a time.

## Scope, honestly bounded

Section 7a's DBA Console describes batch actions across many tables
**and** many environments ("250 tables x 7 environments" in the original
framing). This project has no multi-environment concept to act on — one
Postgres connection, one Oracle introspection source. Building a
screen that pretends to span environments that don't exist would be
worse than not building it. What's real and buildable is the multi-
*table* half: reviewing sensitivity and proposing masking across many
tables in one action instead of clicking through Masking Designer table
by table. That's what this ships. The environment dimension stays an
open, named gap (see "What was intentionally NOT done" below), not a
silently absorbed one.

## Decision

- **No backend changes.** `DBAConsole` (`ui/console.jsx`) calls
  `GET /api/masking/tables` (already existed) to list every table with
  sensitive columns, and `POST /api/masking/{table}/propose` (already
  existed) once per checked table when "Propose masking for N tables" is
  clicked. Same reuse pattern as the Approval Dashboard (ADR-0010) —
  building a new bulk endpoint would have duplicated logic the per-table
  endpoint already has correct and tested.
- **Proposes, never applies** — the same discipline `MaskingAgent` itself
  follows (ADR-0004: "proposes... never applies unreviewed"). The summary
  panel says so explicitly: review each table's exact strategy in Masking
  Designer before treating a bulk proposal as final.
- **OPERATOR-gated**, not ADMIN — proposing a masking policy is the same
  trust level as Agent Console's `plan`/`preview` actions (ADR-0008),
  not a write-authorizing action like confirm/execute.
- **Coverage computed client-side** the same way `MaskingDesigner`
  already does (`rules.length / sensitive_columns.length`), so the two
  screens can't silently disagree about what "100% covered" means.

## Verification

The standard bundle + SSR check ran clean across all 8 screens. For the
API calls themselves: an earlier attempt to verify against a real booted
`uvicorn` process hit the same background-process/shell-timeout flakiness
noted in ADR-0010 and ADR-0011 — rather than fight it a third time or
present an inconclusive result as confirmed, verification moved to a
deterministic in-process `TestClient` call against the exact two
endpoints this screen uses, confirming the response shapes
(`{table, sensitive_columns}` / `{rules: [{column, strategy, salt}]}`)
match what the new JS code reads field-for-field. Both endpoints were
already independently verified live in ADR-0006/0009 for other screens
that call them — this pass confirmed the *new* caller's expectations
match, not re-litigated whether the endpoints themselves work.

## Consequences

- An ADMIN/OPERATOR can now get a fleet-level read on masking coverage
  across the whole Provider schema in one screen, instead of inferring it
  by opening Masking Designer once per table.
- This is the third time in a row (ADR-0010, 0011, 0013) that a live
  `curl`-against-`uvicorn` verification attempt hit the same kind of
  background-process flakiness in this sandbox. Worth noting explicitly
  as an environment characteristic to plan around, not re-discover each
  time: prefer `TestClient` for anything that doesn't specifically need a
  real listening socket, and reserve live-process verification for the
  handful of things that genuinely require it (e.g. confirming a
  `warnings.warn` fires at real import time, ADR-0009).

## What was intentionally NOT done this pass

- **No multi-environment dimension** — see "Scope, honestly bounded"
  above. This is the biggest remaining gap between this screen and
  Section 7a's actual description, and it's a real architectural gap
  (this project has one target database, not N), not a UI omission.
- No "apply to target database" action from this screen — proposing is
  as far as any masking UI in this project goes; actually writing masked
  data still requires the Agent Console's full
  Plan→Explain→Preview→Confirm→Execute→Report workflow, on purpose.
- No persistence of a bulk proposal across a page reload — like
  `MaskingDesigner`'s per-table proposals, results live in component
  state only.
