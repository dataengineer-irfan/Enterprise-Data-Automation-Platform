"""
agent/subagents/schema_metadata_agent.py — Section 2.1's Schema/Metadata Agent.

Tools it owns: `introspect_schema`, `resolve_glossary_term`, `build_fk_graph`.

This subagent is a thin, scoped wrapper around Phase 1's already-built
introspection (`OracleDDLAdapter`), glossary (`core.glossary`), and FK
graph (`core.schema_graph`) — Phase 4 doesn't reinvent that logic, it just
exposes it behind the dispatch-by-task-action contract every subagent
shares, so the Manager can call it identically to every other subagent.
"""
from __future__ import annotations

from typing import Any

from adapters.oracle_ddl_adapter import OracleDDLAdapter
from agent.subagents.base import Subagent, SubagentResult
from agent.shared_storage import SharedStorage
from core.glossary import Glossary
from core.schema_graph import SchemaGraph


class SchemaMetadataAgent(Subagent):
    name = "schema_metadata_agent"

    def __init__(self, storage: SharedStorage, adapter: OracleDDLAdapter, graph: SchemaGraph, glossary: Glossary) -> None:
        super().__init__(storage)
        self._adapter = adapter
        self._graph = graph
        self._glossary = glossary
        self._tables_cache: dict | None = None

    def _tables(self) -> dict:
        if self._tables_cache is None:
            self._tables_cache = {t.name.lower(): t for t in self._adapter.introspect_schema("provider")}
        return self._tables_cache

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "introspect_schema":
            return self.introspect_schema(task["table"])
        if action == "resolve_glossary_term":
            return self.resolve_glossary_term(task["term"])
        if action == "build_fk_graph":
            return self.build_fk_graph(task["table"])
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    def introspect_schema(self, table: str) -> SubagentResult:
        tables = self._tables()
        node = tables.get(table.lower())
        if not node:
            return SubagentResult(agent=self.name, action="introspect_schema", success=False,
                                   error=f"Table '{table}' not found.")
        detail = {
            "name": node.name,
            "columns": [{"name": c.name, "type": c.data_type, "nullable": c.nullable, "pk": c.is_primary_key} for c in node.columns],
            "primary_key": node.primary_key,
            "foreign_keys": [{"fk": fk.fk_name, "columns": fk.child_columns, "parent": fk.parent_table} for fk in node.foreign_keys],
        }
        result = self._store_detail("introspect_schema", detail, record_count=len(node.columns))
        result.summary = {"table": node.name, "column_count": len(node.columns), "pk": node.primary_key,
                           "fk_count": len(node.foreign_keys)}
        return result

    def resolve_glossary_term(self, term: str) -> SubagentResult:
        resolved = self._glossary.resolve_glossary_term(term)
        if not resolved:
            return SubagentResult(agent=self.name, action="resolve_glossary_term", success=False,
                                   error=f"No glossary entry for '{term}'.")
        return SubagentResult(
            agent=self.name, action="resolve_glossary_term", success=True,
            summary={"term": resolved.business_term, "table": resolved.physical_table, "column": resolved.physical_column,
                     "definition": resolved.definition},
        )

    def build_fk_graph(self, table: str) -> SubagentResult:
        table = table.lower()
        node = self._graph.tables.get(table.upper())
        if not node:
            return SubagentResult(agent=self.name, action="build_fk_graph", success=False,
                                   error=f"Table '{table}' not found in schema graph.")
        detail = {
            "table": table,
            "parents": sorted(self._graph.parents_of(table.upper())),
            "children": sorted(self._graph.children_of(table.upper())),
            "required_for_active_status": node.required_for_active_status,
        }
        result = self._store_detail("build_fk_graph", detail, record_count=len(detail["parents"]) + len(detail["children"]))
        result.summary = {"table": table, "parent_count": len(detail["parents"]), "child_count": len(detail["children"])}
        return result
