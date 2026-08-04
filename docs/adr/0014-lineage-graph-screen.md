# ADR 0014 — Lineage Graph Screen

## Status
Accepted

## Context
The platform already includes a Phase 5 UI shell with a dual-mode React console (`ui/console.jsx`) that can run against a live backend or fall back to deterministic demo data. Prior ADRs covered the UI kickoff, API wiring, auth/RBAC, CORS, approval dashboard, session expiry and approval badge, bulk approval, and DBA console progress.

One remaining Phase 5/6 gap was the absence of a dedicated Lineage screen. The Schema Explorer already shows parent/child relationships in a lightweight custom SVG diagram, but there is no standalone screen for lineage exploration.

## Decision
Add a dedicated `Lineage` screen to `ui/console.jsx` with the following behavior:

- new sidebar nav item labeled `Lineage`
- uses the existing `useApiHealth()`/`apiGet()` dual-mode fetch pattern
- when connected live, fetches `/api/schema/tables/{selectedTable}` and renders its `parents` and `children`
- when offline/demo, falls back to the existing `SCHEMA` mock metadata
- renders a simple SVG node-and-edge graph with clickable nodes to focus another table
- preserves the console's existing live/demo semantics and adds an optional,
  client-only React Flow integration for browser builds while keeping an
  SSR-safe SVG fallback for bundle/SSR verification

## Verification
- Focused regression test added in `tests/test_phase5g_lineage_graph.py` asserting the presence of `function LineageGraphScreen`, `Lineage` in nav, and SVG markup in `ui/console.jsx`.
- Verified the UI bundle compiles with `esbuild` and `react-dom/server` successfully renders every screen, including the new `Lineage` variant.
- Verified the SQL Editor now supports offline/demo preview generation from built-in mock schema metadata.

## What was intentionally not done
- Implemented a lightweight interactive SVG lineage graph with hover,
  click, drag, and zoom support; a client-only React Flow integration is now
  available when the browser frontend loads the package, preserving the
  existing SVG fallback for SSR.
- Did not add interaction-level UI tests for the new screen beyond bundle/SSR verification.
- Did not replace the local auth provider or change auth/CORS defaults.
