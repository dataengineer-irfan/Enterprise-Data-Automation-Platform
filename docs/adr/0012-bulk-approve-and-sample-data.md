# ADR-0012: Bulk Approve/Reject + Sample-Data Quality

**Status:** Accepted
**Scope:** the last two items on the pending list from ADR-0011/README —
no bulk action on the Approvals screen, and `backend/sample_rows.py`
falling back to random English words for common audit/system columns.

## Bulk approve/reject

Per-plan checkboxes on the Approvals screen, a "Select all" toggle, and
"Approve all" / "Reject all" buttons that appear only once something's
checked. Deliberately **sequential per plan, not a shortcut around the
workflow**: each selected plan still goes through the real
`confirm -> execute` HTTP sequence one at a time — "bulk" here means "the
ADMIN doesn't have to click through N screens for N plans currently
sitting in the same queue," not "skip Section 2.2 rule #1 for N plans at
once." Partial failure is handled explicitly: the result message reports
`succeeded/total` and lists which specific plan IDs failed and why,
rather than an all-or-nothing outcome that would hide which of N plans
actually went through.

One structural fix needed to make this work cleanly: each row was
previously a single `<button>` (the whole row was the click target to
open detail). Adding a checkbox meant that could no longer be one
interactive element nesting another — invalid HTML, and unreliable event
bubbling for "did they click the checkbox or the row." Restructured to a
`<div>` row containing a standalone `<input type="checkbox">` (with
`stopPropagation` on its own click) and a separate inner `<button>` for
the open-detail action. Caught by review before it became a real bug, not
after — nested interactive controls are exactly the kind of thing that
"looks fine, esbuild bundles it fine, SSR even renders it fine" while
being wrong at actual click-time, which none of this project's existing
verification (bundle + SSR) would have caught. Worth remembering: the
esbuild/SSR check catches crashes and markup errors, not interaction bugs
— there is currently no substitute in this project's toolchain for
actually clicking things, short of the `jsdom` + real-event approach
ADR-0011 used for the session-expiry fix. Not applied here given the
"finish quickly" framing this pass was scoped under; flagged as a gap in
this project's *testing* coverage, not just its feature coverage.

## Sample-data quality (`backend/sample_rows.py`)

Previously: any column not matching a small set of name/type patterns
(SSN/TIN/NPI/license/state/date/code/numeric) fell straight to
`fake.word()` — so nearly every table's audit columns (`g_aud_user_id`,
`g_aud_ts`, `g_aud_add_ts`, `p_tax_rpt_ind`, `l_hibernate_ver_num`, every
`*_sk` surrogate key) showed up as plain English words in the Masking
Designer / Schema Explorer previews. Harmless — clearly labeled sample
data — but looked like a bug at a glance, and was flagged as exactly that
in ADR-0007 and every README since.

Added real patterns for the column shapes that actually recur across
nearly every table in this schema (confirmed by inspecting the real DDL,
not guessed): `*_user_id` → a plausible username, `*_ts`/`TIMESTAMP` → a
real datetime, `*_ind` → `Y`/`N`, `*_sk`/`*_seq_num` → a surrogate-key-
shaped integer, `*ver_num` → a small version number. Verified directly
against `p_alt_id_tb` (the table used throughout this project's own
examples): every column now produces a plausible value except `p_alt_id`
itself, which genuinely can't be inferred from its column name alone —
its real meaning (SSN, NPI, DEA, etc.) depends on the *value* of a sibling
column (`p_alt_id_ty_cd`) at row-generation time, which a per-column,
name-only heuristic structurally cannot see. Documented as a real,
narrower limitation rather than silently left as "still sometimes wrong"
the way the original broader complaint was.

## Verification

Bulk actions: full `esbuild` bundle check + `react-dom/server` SSR render
across all 7 screens (unchanged from every prior UI pass in this
project). Sample data: direct inspection of `generate_sample_rows()`
output against the real introspected `p_alt_id_tb` metadata, confirming
every previously-word-salad column now produces a value matching its
actual shape. Full 101-test Python suite re-run and unaffected (both
changes are additive — new patterns/new UI branches, no existing
behavior removed).

## Consequences

- An ADMIN triaging several pending plans at once no longer has to open,
  confirm, and execute each one individually.
- Preview data across every screen that uses `generate_sample_rows()`
  (Masking Designer, Agent Console's sample-backed preview) now looks
  like what it's meant to represent for the overwhelming majority of
  columns in this schema.

## What was intentionally NOT done this pass

- No interaction-level (click/event) test coverage was added for the new
  checkbox UI — see the note above; this is a real, acknowledged gap in
  this project's *test tooling*, not just a missing feature.
- `p_alt_id`-shaped ambiguity (a column's real meaning depending on a
  sibling column's value) is not solved generally — `generate_sample_rows`
  still operates one column at a time, with no cross-column awareness.
- No bulk action anywhere else (Job Monitor, Audit Log) — only Approvals,
  which is the one screen where "act on several of these at once" is the
  actual use case.
