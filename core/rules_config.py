"""
core/rules_config.py — Reads the `sensitive: true` flags already present in
config/generation_rules.yaml (the project's existing column-generation
rules file) and turns them into the explicit sensitive-column set that
`SensitivityClassifier` uses (Section 5: "combines name heuristics, the
business glossary, and FK-graph propagation").

Each rule in generation_rules.yaml can carry an `applies_to.column`
(physical column, e.g. `p_alt_id`) — that's what actually gets masked, not
the YAML key (which is often a semantic name like `p_alt_id_ssn_tin`).
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_sensitive_columns(config_dir: Path, filename: str = "generation_rules.yaml") -> set[str]:
    path = config_dir / filename
    if not path.exists():
        return set()

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    sensitive: set[str] = set()
    for rule_key, rule_def in (data.get("columns") or {}).items():
        if not isinstance(rule_def, dict) or not rule_def.get("sensitive"):
            continue
        applies_to = rule_def.get("applies_to") or {}
        column = applies_to.get("column") or rule_key
        sensitive.add(column.lower())
    return sensitive
