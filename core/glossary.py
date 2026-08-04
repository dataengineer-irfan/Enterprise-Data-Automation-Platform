"""
core/glossary.py — Business glossary + reference-value (valid-values) model.

Backs the "Understands business terminology, schema, metadata, relationships
and constraints automatically" requirement (Section 1) and is what the
Schema/Metadata Agent's `resolve_glossary_term` tool will call in Phase 3+.

Sources (Phase-1, file-based; Phase-2+ can point these at live
R_VV_TB / R_VV_DOMAIN_TB / R_LIST_HDR_TB / R_LIST_DTL_TB rows instead):

  - config/glossary.csv        -> GlossaryTerm rows (business_term -> physical column)
  - config/reference_data.csv  -> ReferenceValue rows (valid values per category)

NOTE on knowledge-base conflicts (recorded properly in ADR-0001, summarized
here so the code and the decision trail agree): the project KB contains two
different pictures of the Provider schema —

  1. glossary.csv / reference_data.csv / mmis_schema.xlsx describe a
     simplified PROVIDER / PROVIDER_TAXONOMY / PROVIDER_ADDRESS / ... model.
  2. relationships_verified.yaml / rules.yml / the input/ddl/*.sql files
     describe the REAL, verified P_DTL_TB / P_ALT_ID_TB / ... MMIS schema,
     explicitly marked as superseding the earlier inferred model.

This loader keeps both queryable rather than silently discarding one: the
simplified model is exposed as `legacy_terms` (useful as a plain-English
glossary) while `schema_graph.py` treats relationships_verified.yaml as the
authoritative physical structure. Callers doing real column-level work
should go through `schema_graph.py`, not assume glossary.csv's
`physical_mapping` column resolves against the live DDL.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class GlossaryTerm:
    term_id: str
    business_term: str
    definition: str
    physical_table: Optional[str]
    physical_column: Optional[str]


@dataclass(frozen=True)
class ReferenceValue:
    code_id: str
    category: str
    code: str
    description: str
    active: bool


class Glossary:
    def __init__(self) -> None:
        self._terms_by_label: dict[str, GlossaryTerm] = {}
        self._terms_by_id: dict[str, GlossaryTerm] = {}
        self._ref_by_category: dict[str, list[ReferenceValue]] = {}

    # -- loading --------------------------------------------------------
    def load_glossary_csv(self, path: Path) -> "Glossary":
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mapping = (row.get("physical_mapping") or "").strip()
                table, _, column = mapping.partition(".")
                term = GlossaryTerm(
                    term_id=row["term_id"].strip(),
                    business_term=row["business_term"].strip(),
                    definition=row["definition"].strip(),
                    physical_table=table or None,
                    physical_column=column or None,
                )
                self._terms_by_label[term.business_term.lower()] = term
                self._terms_by_id[term.term_id] = term
        return self

    def load_reference_data_csv(self, path: Path) -> "Glossary":
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rv = ReferenceValue(
                    code_id=row["code_id"].strip(),
                    category=row["category"].strip(),
                    code=row["code"].strip(),
                    description=row["description"].strip(),
                    active=row["active"].strip().lower() == "true",
                )
                self._ref_by_category.setdefault(rv.category, []).append(rv)
        return self

    # -- resolution -------------------------------------------------------
    def resolve_glossary_term(self, business_term: str) -> Optional[GlossaryTerm]:
        """Look up a business term case-insensitively (e.g. 'NPI', 'Provider Status')."""
        return self._terms_by_label.get(business_term.strip().lower())

    def valid_values(self, category: str, active_only: bool = True) -> list[ReferenceValue]:
        values = self._ref_by_category.get(category, [])
        if active_only:
            values = [v for v in values if v.active]
        return values

    def is_valid_code(self, category: str, code: str) -> bool:
        return any(v.code == code for v in self.valid_values(category))

    def all_terms(self) -> list[GlossaryTerm]:
        return list(self._terms_by_id.values())


def load_default_glossary(config_dir: Path) -> Glossary:
    g = Glossary()
    glossary_csv = config_dir / "glossary.csv"
    reference_csv = config_dir / "reference_data.csv"
    if glossary_csv.exists():
        g.load_glossary_csv(glossary_csv)
    if reference_csv.exists():
        g.load_reference_data_csv(reference_csv)
    return g
