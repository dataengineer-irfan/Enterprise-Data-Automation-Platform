"""
core/schema_graph.py — Builds the Provider-module FK/PK relationship graph.

This is the Phase-1, file-based stand-in for the Schema/Metadata Agent's
`build_fk_graph` tool (Section 2.1). It loads
`config/relationships_verified.yaml` — the project's ground-truth export
("Every relationship below is a VERIFIED constraint from the live SIT
database, not inferred from naming conventions") — rather than the older
inferred relationships.yaml / rules.yml files, per that document's own
`important_notes`.

Provides:
  - SchemaGraph.tables               -> {table_name: TableNode}
  - SchemaGraph.children_of(table)   -> tables whose FK points at `table`
  - SchemaGraph.parents_of(table)    -> tables `table` has FKs into
  - SchemaGraph.topological_insert_order() -> safe insert order (parents first)
  - SchemaGraph.tables_required_for_active() -> tables BR-REL-001 depends on
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ForeignKey:
    fk_name: str
    child_table: str
    child_columns: list[str]
    parent_table: str
    parent_columns: list[str]


@dataclass
class TableNode:
    name: str
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    required_for_active_status: bool = False


class CycleError(Exception):
    """Raised by topological_insert_order() if the FK graph has a real cycle
    that isn't resolvable by deferring FK application (mirrors the
    DDL-converter's "apply all FKs after all tables exist" strategy)."""


class SchemaGraph:
    def __init__(self) -> None:
        self.tables: dict[str, TableNode] = {}
        self.core_table: Optional[str] = None
        self.core_primary_key: list[str] = []
        self.business_rules: list[dict] = []

    # -- loading --------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: Path) -> "SchemaGraph":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        graph = cls()
        core = data.get("core_table") or {}
        graph.core_table = core.get("table")
        graph.core_primary_key = core.get("primary_key", [])
        graph.business_rules = data.get("business_rules", [])

        for table_name, tdef in (data.get("tables") or {}).items():
            node = TableNode(
                name=table_name,
                primary_key=tdef.get("primary_key", []) or [],
                required_for_active_status=bool(tdef.get("required_for_active_status", False)),
            )
            for fk in tdef.get("foreign_keys", []) or []:
                node.foreign_keys.append(
                    ForeignKey(
                        fk_name=fk["fk_name"],
                        child_table=table_name,
                        child_columns=fk["child_columns"],
                        parent_table=fk["references_table"],
                        parent_columns=fk["parent_columns"],
                    )
                )
            graph.tables[table_name] = node

        return graph

    # -- graph queries --------------------------------------------------------
    def parents_of(self, table: str) -> set[str]:
        node = self.tables.get(table)
        if not node:
            return set()
        return {fk.parent_table for fk in node.foreign_keys if fk.parent_table != table}

    def children_of(self, table: str) -> set[str]:
        return {
            name
            for name, node in self.tables.items()
            for fk in node.foreign_keys
            if fk.parent_table == table and name != table
        }

    def tables_required_for_active(self) -> list[str]:
        return sorted(n.name for n in self.tables.values() if n.required_for_active_status)

    def foreign_keys_of(self, table: str) -> list[ForeignKey]:
        node = self.tables.get(table)
        return node.foreign_keys if node else []

    # -- ordering --------------------------------------------------------
    def topological_insert_order(self) -> list[str]:
        """
        Kahn's-algorithm topological sort over the parent-FK edges, so a
        caller can insert parents before children. Self-referencing FKs
        (e.g. P_AFFL_TB -> P_DTL_TB twice) and tables outside this module's
        export (G_*, R_*, T_*) are treated as already-satisfied roots,
        mirroring the reused DDL converter's "defer all FKs, apply after
        every table exists" strategy — so this never raises for the kind
        of soft cycles that show up in a real 100+ table export.
        """
        in_degree: dict[str, int] = {t: 0 for t in self.tables}
        edges: dict[str, set[str]] = {t: set() for t in self.tables}

        for name, node in self.tables.items():
            for fk in node.foreign_keys:
                parent = fk.parent_table
                if parent == name or parent not in self.tables:
                    continue  # self-ref or external (G_*/R_*/T_*) — not part of this ordering
                if name not in edges[parent]:
                    edges[parent].add(name)
                    in_degree[name] += 1

        ready = sorted([t for t, deg in in_degree.items() if deg == 0])
        order: list[str] = []
        while ready:
            ready.sort()
            current = ready.pop(0)
            order.append(current)
            for nxt in sorted(edges[current]):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    ready.append(nxt)

        if len(order) != len(self.tables):
            remaining = set(self.tables) - set(order)
            raise CycleError(f"Unresolvable FK cycle among: {sorted(remaining)}")
        return order


def load_default_schema_graph(config_dir: Path) -> SchemaGraph:
    return SchemaGraph.from_yaml(config_dir / "relationships_verified.yaml")
