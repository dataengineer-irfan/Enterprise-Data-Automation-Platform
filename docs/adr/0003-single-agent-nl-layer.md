# ADR-0003: Single-Agent NL Layer (Phase 3) — Ollama Default, qwen2

**Status:** Accepted
**Scope:** Phase 3 — "one agent (the Manager, no subagents yet) that can
plan/explain/preview/confirm/execute for a single-table job" (Section 9).
Per the spec, this phase needed a check-in on the LLM provider decision
before starting; that confirmation was given explicitly: **Ollama as the
default provider, `qwen2` as the model for now** (not the spec's suggested
`qwen3:8b` — an explicit, documented deviation, not an oversight).

## Decision

- **`core/llm_provider.py`** — `LLMProvider` ABC, two implementations:
  - `OllamaProvider` (default): talks to `http://localhost:11434/v1` via
    stdlib `urllib`, zero extra dependency, zero external account. Model
    default is `qwen2:7b`. Fails gracefully — a network error returns a
    structured `LLMResponse(error=...)`, never raises past the provider
    boundary. Confirmed by test against an unreachable port.
  - `ClaudeProvider` (opt-in): requires `ANTHROPIC_API_KEY` and the
    `anthropic` package, which is deliberately **not** in
    `requirements.txt` — importing it lazily and raising a clear
    "add it with an ADR first" error keeps Section 10.1's lock-file
    discipline intact instead of silently growing the dependency set.
  - `load_default_provider()` reads `LLM_PROVIDER` (default `ollama`).
- **`agent/plan_memory.py`** — one JSON file per plan, same "one seam"
  pattern as `AuditLog` (ADR-0001). `PlanStatus` enum enforces the
  Plan→Explain→Preview→Confirm→Execute→Report sequence: `execute()`
  re-reads status from disk and refuses anything not `CONFIRMED` — a
  caller cannot skip confirmation by just calling `execute()` with a
  plan object it already has in memory, since the object's in-memory
  status isn't trusted, only the persisted one is. Confirmed by test.
- **`agent/manager.py`** — the LLM is used **only** for intent
  classification / planning, via a strict JSON-object response contract
  (`core.llm_provider.extract_json_object` tolerates markdown fences and
  garbage, since local models don't reliably follow "respond with ONLY
  JSON"). Every actual data operation (introspect/validate/mask) reuses
  the exact same deterministic Phase 1/2 code the CLI calls — the agent
  layer never gives an LLM direct data access. This is the deliberate
  mitigation for Section 8.1's own warning that `qwen2`/`qwen3:8b` are
  "noticeably weaker... at multi-step, ambiguous planning" than Claude: a
  bad plan here produces a bad *preview* that a human then rejects, not a
  bad *write*.
- **Ambiguity handling (Section 2.2 rule #4):** if the model doesn't name
  a table, names one that doesn't exist, or returns unparseable output,
  the Manager sets `intent="unclear"` and always attaches a
  `clarifying_question` — it never guesses a destructive default. Covered
  by 4 separate tests (unknown table, missing table, unreachable LLM,
  garbage response).
- **`chat.py`** — the Phase 3 interactive surface (Section 9 doesn't
  require a UI yet — that's Phase 5). A REPL that walks the full
  Plan→Explain→Preview→Confirm→Execute→Report sequence against a CSV file,
  using `input()` as the injected confirmation callback.

## Why `qwen2` instead of the spec's `qwen3:8b`

Explicit instruction, not a technical constraint discovered mid-build.
Recorded here so a future pass doesn't "silently re-decide the
architecture" (the exact failure mode Section 10.2 exists to prevent):
swapping back to `qwen3:8b`, or up to `qwen3-coder:30b`, is a one-line
`LLM_MODEL` env var change — `OllamaProvider` doesn't hardcode the model
name anywhere else, by design.

## Consequences

- No live Ollama instance exists in this sandbox; every Manager-workflow
  test uses a `FakeLLMProvider` returning canned JSON, so agent *safety*
  logic (confirmation gating, ambiguity handling, plan persistence) is
  fully covered without a live model. Only `OllamaProvider` itself is
  tested against a real (deliberately unreachable) socket, to prove the
  graceful-failure path works. Validating actual `qwen2:7b` planning
  quality against real natural-language requests still needs to happen
  against a real running Ollama instance — flagged as a follow-up, not
  simulated here.
- `verify_fk_consistency` and the rest of Phase 2 are unchanged and now
  sit *underneath* the agent — `ManagerAgent.preview()`/`execute()` call
  the same `MaskingEngine`/`ValidationEngine` the Phase 2 CLI calls, so
  Phase 2's guarantees (deterministic, FK-safe masking) carry through
  automatically rather than being reimplemented per-agent.

## What was intentionally NOT built this pass

- Subagents (Schema/Metadata, Profiling/Mapping, Validation, Correction,
  Masking, SQL Generation, Execution/Report) — Phase 4, gated, needs its
  own check-in per the spec ("multi-agent adds real infra complexity —
  confirm you actually have jobs that need it before building it").
- SQL generation / execution against a live target database from a
  confirmed plan (Section 6) — Phase 3's "execute" step, for a mask job,
  means "write a masked CSV," matching Phase 2's scope; it does not yet
  generate or run INSERT/UPDATE/MERGE SQL.
- Any UI (Phase 5) — `chat.py` is a REPL, not the AI Chat Panel from
  Section 7.
- Condensed-result handoff pattern (Section 2.2 rule #5) — not yet
  relevant with a single agent and no subagents to hand results to.
