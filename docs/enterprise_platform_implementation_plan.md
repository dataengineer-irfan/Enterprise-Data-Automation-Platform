# Enterprise Platform Implementation Plan

## Objective
Build a platform-first Enterprise Test Data Platform for Oracle → Oracle that provides metadata discovery, dependency-aware synthetic data generation, referential integrity preservation, validation, auditing, and reporting, with a clear path to future database extensibility.

## Architecture Principles
- Platform-first, not wizard-first.
- Capability-based navigation.
- Onboarding is a supported capability, not the primary flow.
- Reuse existing React architecture, routing, layouts, and components wherever possible.
- Refactor incrementally so existing functionality continues to work.
- Maintain backward compatibility with current APIs unless a replacement endpoint is implemented.
- Use feature-based modular architecture.
- Keep business logic separate from UI components.
- Keep UI components reusable, composable, and independent.
- Support future expansion without requiring architectural redesign.

## Primary Capabilities
- Home
- Connections
- Workspaces
- Metadata Catalog
- Discovery
- Data Generation
- Referential Integrity
- Validation
- Auditing & Reporting
- Monitoring
- Administration

## Enterprise Enhancements
### Metadata Catalog
- Persist all discovered database metadata in PostgreSQL.
- Use the metadata catalog as the single source of truth for the platform.
- Catalog Connections, Schemas, Tables, Columns, PK/FK, Views, Procedures, Functions, Packages, Dependencies, Statistics, and Snapshots.

### Metadata Snapshots
- Support versioned metadata snapshots per workspace.
- Enable comparison, schema drift detection, rollback, audit, and reproducibility.
- Treat snapshots as first-class entities linked to workspaces and jobs.

### Enterprise Job Engine
- Treat Discovery, Generation, Validation, and Reporting as background jobs with lifecycle tracking.
- Jobs should support states: Pending, Queued, Running, Completed, Failed, Cancelled.
- Use Jobs as the backbone for monitoring and operational visibility.

### Workspace Isolation
- All operations execute inside a Workspace containing:
  - Source Connection
  - Target Connection
  - Metadata Snapshot
  - Generation Configuration
  - Jobs
  - Reports
  - Audit History

## Enterprise UI Components
- Persistent status bar with platform state.
- Home / Welcome Center with readiness and next action recommendations.
- Connections screen with source/target connector CRUD.
- Workspace screen with source, target, snapshot, owner, and lifecycle status.
- Guided metadata discovery screen.
- Data generation control panel with table/row/masking/relationship options.
- Execution trace and run report pages.
- Audit and report dashboards.
- Operational monitoring dashboards.
- Empty states with clear recovery actions.
- Loading, error, and success states on every page.
- Long-running operations with progress, status updates, cancel, retry, and completion summary.

## What is Missing Today
The current implementation is a strong foundational prototype, but the MVP enterprise product still needs:

- Oracle Source Connection management.
- Oracle Target Connection management.
- Workspace-driven test data generation lifecycle.
- Metadata discovery with snapshot persistence.
- Central metadata catalog.
- Relationship graph and PK/FK discovery.
- Dependency graph and referential integrity engine.
- Data generation configuration and masking rules.
- Execution jobs and progress tracking.
- Execution trace and audit history.
- Validation and comparison reporting.
- Data quality reports.

## Workspace Boundaries
- All user operations must execute within a Workspace context.
- Discovery, Generation, Validation, Reports, Audit, and Jobs must be workspace-scoped.
- Explicit workspace boundaries prevent ambiguity as the platform grows.

## Enterprise Workflow
Every generation run must follow the same lifecycle.

1. Create Workspace
2. Configure Source Oracle Connection
3. Test Source Connection
4. Discover Source Metadata
5. Profile Source Data
6. Persist Metadata Snapshot
7. Configure Target Oracle Connection
8. Test Target Connection
9. Select Tables
10. Configure Generation Rules
   - Row Counts
   - Masking
   - Lookup Rules
   - Referential Integrity
11. Build Dependency Graph
12. Execute Data Generation
13. Validate Generated Data
14. Produce Reports
15. Persist Audit History
16. Review Results

## Recommended Roadmap
### Phase 1 — Platform Foundation
- Build Connection Manager for source and target.
- Add Oracle source connector with a test-connection flow.
- Add metadata catalog persistence.
- Introduce Workspace Management.
- Enforce workspace as the primary context for all work.
- Add source/target pairing and metadata snapshot support.

### Phase 2 — Discovery & Catalog
- Implement live metadata discovery.
- Store discovered metadata in PostgreSQL catalog.
- Surface discovery progress and snapshot counts.
- Add schema explorer, table metadata, and relationship graph UX.

### Phase 3 — Data Generation & Integrity
- Add data generation controls by workspace.
- Support table selection, row counts, masking, and relationship preservation.
- Build referential integrity ordering and dependency execution.
- Ensure generation never violates FK constraints.

### Phase 4 — Execution & Validation
- Add execution pipeline and background job tracking.
- Integrate validation checks into generation.
- Add execution trace and run reporting.
- Build audit summaries and comparison reports.

### Phase 5 — Monitoring & Scale
- Add operational dashboards for jobs, health, metrics, and queue state.
- Add comparison reports source vs generated.
- Add relationship and data quality reports.
- Plan extension to additional targets after the Oracle → Oracle MVP is stable.

## Phase Exit Criteria
### Phase 1 Exit
- Source connection works.
- Target connection works.
- Metadata discovery completes successfully.
- Workspace can be created.
- Metadata snapshot persists in the catalog.
- Basic workspace flow operates end to end.

### Phase 2 Exit
- Cataloged metadata can be queried from the metadata catalog.
- Discovery progress and snapshot UX operate correctly.
- Schema and relationship metadata are visible in the UI.
- Data remains available from snapshots without re-querying Oracle.

### Phase 3 Exit
- Workspace-scoped generation can start and complete.
- Referential integrity ordering is applied.
- Masking controls are respected.
- Job status is tracked through the job engine.

### Phase 4 Exit
- Execution pipeline runs as background jobs.
- Validation integrates with generation jobs.
- Reports are generated and accessible.
- Audit history is persisted and reviewable.

### Phase 5 Exit
- Monitoring dashboards display live job and health metrics.
- Comparison and data quality reports are available.
- Additional target planning is defined.

## Execution Priorities
1. Connection Manager (Oracle source + Oracle target)
2. Metadata Discovery Engine
3. Workspace Management
4. Relationship Graph Builder
5. Synthetic Data Generation Engine
6. Referential Integrity Engine
7. Masking Engine
8. Execution Pipeline
9. Validation Engine
10. Audit & Reporting Center
11. Operational Monitoring Dashboard
12. Target extension planning

## Non-Functional Requirements
- Secure credential storage.
- No hardcoded configuration.
- Structured logging.
- Centralized exception handling.
- Pagination for large datasets.
- Lazy loading for metadata.
- Async background jobs.
- Responsive UI.
- Accessibility considerations.
- Configuration through environment variables.

## First Concrete Deliverables
1. `Connections` API and UI screen.
2. `Workspaces` API and UI screen.
3. `Discover Metadata` workspace action.
4. `Generate Data` workspace action with table/row/masking controls.
5. `Job Report` detail page with execution summary.

## Implementation Rules
1. Do NOT redesign the application from scratch.
2. Reuse the existing React architecture, routing, layouts, and components wherever possible.
3. Refactor incrementally so existing functionality continues to work throughout implementation.
4. Maintain backward compatibility with current APIs unless a replacement endpoint is implemented.
5. Do not remove any existing capabilities without providing an equivalent or improved enterprise implementation.
6. Use feature-based modular architecture. Each capability should be implemented as an independent module with its own components, services, hooks, and routes.
7. Keep business logic separate from UI components.
8. Keep UI components reusable, composable, and independent.
9. Support future expansion without requiring architectural redesign.
10. All pages must support:
   - loading state
   - empty state
   - error state
   - success state
11. Every long-running operation must provide:
   - progress indicator
   - status updates
   - cancel capability (where applicable)
   - retry capability
   - completion summary
12. Preserve responsive behavior while optimizing primarily for desktop enterprise users.
13. Follow the existing design system and avoid introducing inconsistent UI patterns.
14. Use production-quality code with clear separation of concerns, maintainability, and scalability.
15. Implement the roadmap phase by phase. Each phase must be fully functional and integrated before moving to the next phase.

## Definition of Done
A phase is complete only when:
- Backend APIs are completed.
- UI is fully integrated into the existing application.
- Role permissions are verified.
- Audit logging is enabled.
- Error handling is implemented.
- Performance is validated.
- No feature flag or demo code remains.
- Navigation works correctly.
- Existing functionality continues to operate.
- New functionality is operational.
- Empty, loading, success, and error states are implemented.
- No placeholder or demo components remain.
- Code follows the existing project structure and conventions.
- The application feels like a production enterprise platform rather than a prototype or demo.
