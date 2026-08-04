# ADR-0001: Provider-Module Foundation (Phase 1)

**Status:** Accepted
**Scope:** Phase 1 Foundation only, per the platform spec's own gating
("Stop and check in before starting Phase 3 / Phase 4"). Agents, the DBA
console UI, masking policies, and additional connectors are explicitly
out of scope for this pass.

## Context

Two things arrived to build from:

1. `FINAL_ENTERPRISE_PLATFORM_PROMPT.md` — the multi-phase spec (hexagonal
   core, `DatabasePort` interface, per-engine adapters, agent swarm, DBA
   console, masking/synthetic data, audit log, ADR discipline).
2. This project's existing knowledge base for the **Provider** module
   (Medicaid MMIS), plus an uploaded working Python tool
   (`oracle-to-postgres-converter`) that already does Oracle→Postgres DDL
   conversion, execution, and FK-aware synthetic data generation against
   the real 109-table Provider DDL export.

Rather than starting from zero, Phase 1 adopts the uploaded converter as
the seed for the Postgres/Oracle connector pair and builds the missing
hexagonal seams (`DatabasePort`, audit log, glossary, schema graph) around
it.

## Decision

- **`core/database_port.py`** defines the engine-agnostic `DatabasePort`
  ABC + shared dataclasses (`TableMetadata`, `ColumnMetadata`,
  `ForeignKeyMetadata`, `ExecutionResult`, `ExplainResult`). No adapter
  imports (psycopg, etc.) are allowed outside `adapters/`.
- **`adapters/postgres_adapter.py`** wraps the existing, working
  `DatabaseManager` / `SQLExecutor` under `DatabasePort`. It adds
  `information_schema`-based introspection and enforces the Section 7a
  source-only write refusal *at the connector layer*, not just in a UI —
  confirmed by test: a `is_source_only=True` connection refuses `execute()`
  and returns a structured failure rather than raising.
- **`adapters/oracle_ddl_adapter.py`** is introspection-only in Phase 1: no
  live Oracle instance is available in this environment (the spec itself
  flags Oracle XE / Always-Free-tier as the dev option). It reuses
  `DDLConverter` to parse the real `input/ddl/*.sql` export and reconciles
  PK/FK against `relationships_verified.yaml` rather than trusting each
  file's own (sometimes one-sided) FK clause.
- **`core/schema_graph.py`** treats `config/relationships_verified.yaml`
  as ground truth, per that file's own header: *"This replaces the
  previous inferred relationships.yaml... VERIFIED constraint from the
  live SIT database."* It provides parent/child lookups, a Kahn's-algorithm
  topological insert order, and `tables_required_for_active()` (currently:
  `P_APPL_STAT_TB, P_ENROL_STAT_TB, P_LIC_CERT_TB, P_SPECL_TB, P_TXNMY_TB,
  P_TY_TB` — BR-REL-001).
- **`core/glossary.py`** loads `glossary.csv` + `reference_data.csv` as a
  plain-English term/valid-value lookup.
- **`core/audit.py`** is an append-only JSON-Lines sink with one write path
  (`AuditLog.record`) and automatic redaction of any key matching
  `pass|pwd|secret|token|api[_-]?key|credential`, confirmed by test.

## Known conflict in the knowledge base (not silently resolved)

`glossary.csv` / `reference_data.csv` / `mmis_schema.xlsx` describe a
simplified `PROVIDER` / `PROVIDER_TAXONOMY` / `PROVIDER_ADDRESS` model.
`relationships_verified.yaml`, `rules.yml`, and the real DDL export
describe the actual MMIS schema (`P_DTL_TB`, `P_ALT_ID_TB`, ...), and the
relationships file explicitly says it **supersedes** the earlier inferred
model. This ADR keeps both queryable instead of deleting one:

- `schema_graph.py` (physical structure, FK/PK, insert order) →
  **always** relationships_verified.yaml / the real DDL.
- `glossary.py` (business-term lookup) → glossary.csv, flagged in its own
  docstring as a simplified/legacy naming layer, **not** a reliable
  column-resolution path.

Follow-up (flagged, not blocking): `data_generator.py`'s `PROVIDER_FK_MAP`
should be diffed against `relationships_verified.yaml` in a later pass —
it was written against the same DDL export but hasn't been cross-checked
against the verified relationships file line-by-line.

## Consequences

- Adding MySQL/SQL Server later means one new file implementing
  `DatabasePort` — no changes to `core/`.
- The synthetic-data generator (`data_generator.py`, `pattern_analyzer.py`)
  and the CSV sample loader (`data_loader.py`) are carried over unchanged;
  they are Phase 2 (masking/synthetic-data) material and are **not**
  wired into the agent layer yet — there is no agent layer yet.
- No UI, no LLM provider abstraction, no masking policy engine in this
  pass — per the spec's own phase gate, this is where Phase 1 stops for
  a check-in.

## What was intentionally NOT built this pass

- Agent swarm / LangGraph orchestration (Phase 3+)
- DBA console UI (Phase 3+)
- Masking policy engine beyond the `sensitive: true` flags already present
  in `rules.yml`
- Live Oracle connectivity (adapter is DDL-file introspection only)
- MySQL / SQL Server / other connectors
