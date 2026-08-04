# ADR-0004: Multi-Agent Fan-Out (Phase 4)

**Status:** Accepted
**Scope:** Phase 4 — "add the specialized subagents from Section 2.1, shared
plan memory, condensed-result handoff pattern, only for jobs the
single-agent version genuinely can't handle well (multi-table,
multi-source, ambiguous mapping)" (Section 9). Proceeding into this phase
was an explicit instruction ("Continue to phase 4"), given after Phase 3
was delivered and the spec's own gate flagged this as the next check-in
point.

## Decision

- **`agent/subagents/`** — one file per Section 2.1 agent
  (`schema_metadata_agent.py`, `profiling_mapping_agent.py`,
  `validation_agent.py`, `correction_agent.py`, `masking_agent.py`,
  `sql_generation_agent.py`, `execution_report_agent.py`), each a thin,
  scoped wrapper around Phase 1/2 engines plus the tool set Section 2.1's
  table assigns it. None of them call an LLM — consistent with the running
  design choice from ADR-0003 (LLM plans/classifies, deterministic code
  touches data). Column-mapping fuzziness (Profiling/Mapping Agent) is
  handled with stdlib `difflib` name-similarity scoring, not an LLM call:
  fast, deterministic, testable without Ollama, and low-confidence matches
  are surfaced for human review rather than silently applied.
- **`agent/shared_storage.py`** — the "shared storage" side of the
  condensed-result handoff pattern (rule #5): every subagent method
  returns a `SubagentResult` with a small `summary` dict plus a
  `detail_pointer` into local JSON blobs. `Plan.subtasks` (persisted via
  `PlanMemory`, extended this phase) only ever stores the condensed
  `to_dict()` form — confirmed by test that a large `introspect_schema`
  column list never lands directly in the plan record, only behind a
  pointer.
- **`agent/subagents/base.py`** — `SubagentResult.runtime_payload` is a
  deliberate escape hatch: some Manager-to-subagent handoffs need a live
  Python object in-process (a `MaskingPolicy`, a list of generated SQL
  statements) that has no business being JSON-serialized into the audit
  log or plan memory. `runtime_payload` is excluded from `to_dict()` by
  design, so it can never leak into a persisted record by accident.
- **Fan-out routing (Section 2.3)** lives in `ManagerAgent.plan()`: the
  system prompt adds a `multi_table_job` intent with a `tables` list. If
  the model returns `multi_table_job` with fewer than two tables, the
  Manager routes it back to `unclear` rather than spinning up the full
  subagent roster for what's actually a single-table job — enforcing
  Section 2.3's "reserve full multi-agent fan-out for genuinely
  unpredictable, multi-table... jobs" as code, not just as a prompt
  instruction (a weak local model saying "multi_table_job" for one table
  shouldn't be trusted blindly).
- **FK-safe multi-table execution**: `ManagerAgent._order_tables_for_execution()`
  reuses Phase 1's `SchemaGraph.topological_insert_order()` to insert
  parents before children regardless of the order the user (or the LLM)
  listed the tables in. Confirmed by test with tables deliberately listed
  child-before-parent.
- **Evaluator-optimizer loop (Section 2.4)**: `ManagerAgent.run_correction_loop()`
  alternates `CorrectionAgent` (propose-only) and `ValidationAgent`
  (report-only) up to `max_iterations`, stopping early if a pass makes no
  further progress rather than looping to the cap uselessly — then hands
  back to a human either way, per the spec's own phrasing.

## Write-authority enforcement (rule #2) — three independent layers, not one

1. **Structural**: no subagent class except `ExecutionReportAgent` holds a
   `DatabasePort` reference at all. `SQLGenerationAgent` produces text; it
   physically cannot execute anything.
2. **Plan-status gate**: `ExecutionReportAgent.execute_sql()` re-reads plan
   status from `PlanMemory` and refuses anything that was never
   `CONFIRMED` (or the Manager's own `EXECUTING` transition that only ever
   follows a `CONFIRMED` read — see bug below). Confirmed by a test that
   calls the agent directly, bypassing the Manager entirely, to prove the
   agent's own gate doesn't depend on the Manager behaving correctly.
3. **Connector-layer refusal**: `DatabasePort.is_source_only` (Phase 1,
   ADR-0001) refuses the write independently of the agent layer. Confirmed
   by test: a `CONFIRMED` plan against a source-only fake adapter still
   writes zero rows.

## Bug found and fixed during this pass: stale in-memory Plan objects

`PlanMemory.update_status()` returns a **new** `Plan` instance; it does
not mutate the object a caller is already holding. `dispatch_subagent()`
originally did `plan.subtasks.append(...); self._plans.write(plan)` —
writing the caller's possibly-stale in-memory `plan` back to disk
**wholesale**, silently clobbering any status transition (e.g.
`AWAITING_CONFIRMATION` -> `CONFIRMED`) that happened via a different code
path after that `plan` object was captured. Concretely: `execute_fan_out()`
called `update_status(..., EXECUTING)` (persisted correctly), then its
first `dispatch_subagent(..., plan=plan)` call inside the loop overwrote
that back to `AWAITING_CONFIRMATION` — the status the stale in-memory
`plan` still held from before confirmation — and the next dispatch (to
`ExecutionReportAgent`) then correctly refused to run, because as far as
persisted storage was concerned, the plan really did just get un-confirmed.

Two integration tests caught this immediately
(`test_fan_out_execute_with_fake_adapter_respects_write_authority`,
`test_execution_agent_refuses_write_against_source_only_adapter`) — both
failed with a `PermissionError` that, on first read, looked like the
*gate* was broken, not the *state sync*. Root-caused by reproducing it in
isolation and diffing in-memory vs. persisted status directly.

**Fix, not a patch**: `dispatch_subagent()` now reads the plan fresh from
disk before appending a subtask, writes the fresh copy back, and merges
the fresh state into the caller's object (`plan.__dict__.update(...)`) so
the caller's reference stays consistent too. The same
`self._sync(plan, self._plans.update_status(...))` pattern was then
applied to **every** `update_status()` call site in `manager.py` (six
total), not just the one that happened to be exercised by a failing test
— the bug class (stale caller-held Plan objects) was general, not
specific to fan-out.

## Consequences

- Every subagent is independently unit-testable (8 tests) and the full
  orchestration — fan-out routing, condensed handoff, FK ordering,
  write-authority gating at all three layers, and the correction loop —
  is covered end-to-end (14 tests) without a live database or live
  Ollama, matching every prior phase's testing posture.
- The stale-Plan-object bug class is now closed platform-wide, not just
  patched at the one call site a test happened to hit — a direct
  consequence of writing integration tests that exercise multi-step state
  transitions, not just individual method contracts.
- SQL generation is INSERT-only in Phase 4 (`SQLGenerationAgent.generate_sql`
  explicitly rejects other operations with a clear message rather than
  silently producing wrong SQL) — UPDATE/UPSERT/DELETE generation is a
  straightforward extension of the same column-ordering logic, deferred
  until a job actually needs it.

## What was intentionally NOT built this pass

- Real dialect-awareness in `SQLGenerationAgent` beyond Postgres-flavored
  quoting/literals — Phase 1's `ddl_converter.py`/`datatype_mapper.py` are
  the natural extension point per-connector, not duplicated here.
- Parallel/concurrent subagent dispatch (Section 2.4 lists
  "Parallelization for independent subagent calls" as a supporting
  pattern) — `run_fan_out_preview`/`execute_fan_out` dispatch sequentially
  per table today; the `dispatch_subagent` contract doesn't preclude
  parallelizing it later, but nothing here does yet.
- Any UI (Phase 5) or DBA Console (Section 7a / Phase 6).
- Live execution against a real Postgres/Oracle instance — `FakeDatabasePort`
  proves the ExecutionReportAgent code path is correct; a live-DB
  integration test is a documented follow-up, same as ADR-0003 flagged for
  live-Ollama planning quality.
