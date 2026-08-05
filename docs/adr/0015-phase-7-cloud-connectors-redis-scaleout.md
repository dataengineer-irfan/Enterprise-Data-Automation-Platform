# ADR-0015: Phase 7 — Cloud Connectors, Redis Scale-out & Container Deployment

## Context
Phase 7 of the platform roadmap requires expanding database engine support to major cloud warehouses (Snowflake, BigQuery, Redshift) and relational databases (MySQL, SQLite), providing Redis-backed shared storage and plan memory options for multi-worker production scale-out, and delivering container deployment manifests (`docker-compose.yml`).

## Decision
1. **Cloud & Multi-Engine Adapters**:
   - Implemented `SnowflakeAdapter`, `BigQueryAdapter`, `RedshiftAdapter`, `MySQLAdapter`, and `SQLiteAdapter` inheriting from `DatabasePort`.
   - Created `get_adapter()` factory in `adapters/__init__.py` to instantiate engine adapters by key.

2. **Distributed Storage & Multi-Worker Support**:
   - Added `RedisSharedStorage` (`agent/shared_storage.py`) for offloading condensed subagent handoff payloads to Redis keys with configurable TTL.
   - Added `RedisPlanMemory` (`agent/plan_memory.py`) for persistent multi-worker orchestrator plan memory.

3. **Production Container Orchestration**:
   - Created `docker-compose.yml` mounting FastAPI backend, static Nginx frontend, PostgreSQL target, Keycloak IdP, Redis cluster, and Ollama LLM service.

## Consequences
- Every connector enforces `is_source_only` write safeguards at the port boundary.
- The platform can scale across multi-worker worker pools backed by Redis without code modifications.
