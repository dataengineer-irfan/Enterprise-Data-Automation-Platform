# ADR-0002: Validation & Masking Engine (Phase 2)

**Status:** Accepted
**Scope:** Phase 2 only — "full validation engine, masking engine with
FK-aware deterministic strategy, no agent yet — plain deterministic
pipeline, CLI-driven" (Section 9). No check-in gate applies here (those are
only required before Phase 3 and Phase 4); this phase builds directly on
the ADR-0001 foundation.

## Context

Section 4 requires a pluggable validation engine (PK/FK/unique/check/type/
length/regex/lookup/custom rules) producing a structured report. Section 5
requires masking that stays referential-integrity-safe: if a parent
column is masked, every FK column pointing at it must be masked with the
*same* deterministic strategy and salt, or joins break after masking.

## Decision

- **`core/validation.py`** — `ValidationRule` is an ABC; `ValidationEngine`
  is just a registered list of rules run against a batch + `TableMetadata`
  + optional `ValidationContext` (schema graph, glossary, pre-loaded target
  keys). `ValidationEngine.default_rules_for(table)` auto-derives the
  structural rules (NOT NULL, PK, length, FK-if-present) straight from
  introspected metadata; anything business-specific (lookup categories,
  regex formats, custom cross-table rules) is registered explicitly by the
  caller. This mirrors Section 4's "pluggable rule classes" requirement
  without hardcoding business rules into the engine itself.
- **`core/masking.py`** — `MaskingEngine.propagate_fk_rules()` copies a
  parent column's exact `MaskingRule` (same `MaskStrategy` + same salt) onto
  every FK column referencing it. `verify_fk_consistency()` is the
  deterministic-pipeline stand-in for "independently re-checked by the
  Validation Agent" (Section 5) — there is no agent yet, so this function
  *is* the check, callable from `cli.py fk-check` or a CI step.
  Confirmed by test (`test_fk_propagation_makes_child_and_parent_masks_match`):
  the same source value masked in parent and (propagated) child columns
  produces byte-identical output.
- **`cli.py`** — Section 9 says Phase 2 is "CLI-driven," so this is the
  only interface this phase ships: `detect-sensitive`, `validate`, `mask`
  (with `--dry-run` for Section 6's before/after preview), `fk-check`.

## Known limitation: format-preserving masking is NOT real FPE

`MaskStrategy.FORMAT_PRESERVING` (`MaskingEngine._format_preserving`) is a
lightweight HMAC-seeded digit/letter-shape-preserving substitution — it is
deterministic and non-reversible without the salt, which satisfies Phase
2's masking requirement, but it is **not** a cryptographically reviewed
FF1/FF3-1 format-preserving encryption implementation. Before any use case
that needs actual FPE guarantees (vs. "masked, stable, and shaped like the
original"), swap in a vetted library (e.g. `pyffx` or a NIST SP 800-38G
compliant implementation) — tracked here, not silently treated as done.

## Bug found and fixed during this pass

`PrimaryKeyRule` originally only treated `None` PK components as missing
(`any(v is None for v in key)`), not empty strings — inconsistent with
`NotNullRule`, which correctly treats `None` and `""` as missing. A CSV
round-trip test caught a *different*, more serious bug first: PK/FK column
names from `relationships_verified.yaml` are Oracle's native
`UPPER_CASE`, while every parsed column and CSV row-dict downstream is
`lower_case` — so `PrimaryKeyRule`/`ForeignKeyRule` were silently
comparing `"P_SYS_ID"` against row keys that only ever had `"p_sys_id"`,
flagging every row as missing its key. Fixed at the source
(`OracleDDLAdapter.introspect_schema`) by lower-casing PK/FK column names
once, at the adapter boundary, rather than patching every consumer.
Both fixes are covered by regression tests
(`test_report_human_readable_and_summary`,
`test_primary_key_rule_flags_missing_and_duplicate_keys`, and the
Phase 1 suite's updated `p_dtl_tb.primary_key == ["p_sys_id"]` assertion).

## Consequences

- Every rule and masking strategy is unit-testable without a live
  database — 22/22 tests pass file-only, matching Phase 1's approach.
- `verify_fk_consistency` gives the platform an enforceable Section 5
  guarantee *today*, without waiting for the Masking/Validation agent pair
  in Phase 3/4 — when those agents exist, they call the same function
  rather than reimplementing the check.
- Section 7a's "masking-coverage %" pre-flight metric
  (`core.masking.masking_coverage`) and "bulk rule application"
  (`MaskingPolicy.apply_bulk_pattern`) are already implemented here, ahead
  of the DBA Console UI (Phase 6) that will surface them, since neither
  depends on UI or agent work.

## What was intentionally NOT built this pass

- Format-preserving *encryption* (real FF1/FF3-1) — flagged above.
- Check-constraint and data-type-coercion validation beyond length
  (deferred to the connector layer per Section 4's own phrasing —
  "data types" checking is adapter-specific and belongs in `DatabasePort`
  dialect validation, not duplicated here).
- Any UI, any agent, any live-DB write path for masked data (Phase 2 is
  explicitly file/CLI-only).
