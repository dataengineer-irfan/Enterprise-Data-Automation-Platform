#!/usr/bin/env python3
"""
cli.py — Phase 2 CLI: validation + masking, no agent (Section 9 Phase 2).

Commands:
  validate           Run the Validation Engine against a CSV batch for a table.
  mask                Apply a MaskingPolicy (YAML ruleset) to a CSV batch.
  detect-sensitive    List sensitive columns for a table (heuristic + config flags).
  fk-check            Verify FK-consistency across a set of masking rulesets.

Examples:
  python cli.py detect-sensitive --table p_dtl_tb
  python cli.py validate --table p_dtl_tb --input sample.csv
  python cli.py mask --table p_dtl_tb --input sample.csv --ruleset ruleset.yaml \
      --output masked.csv --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

from core.database_port import TableMetadata
from core.glossary import load_default_glossary
from core.masking import (
    MaskingEngine,
    MaskingPolicy,
    MaskingRule,
    MaskStrategy,
    SensitivityClassifier,
    masking_coverage,
)
from core.rules_config import load_sensitive_columns
from core.schema_graph import load_default_schema_graph
from adapters.oracle_ddl_adapter import OracleDDLAdapter
from core.validation import ValidationContext, ValidationEngine

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"


def _load_table(table_name: str) -> TableMetadata:
    graph = load_default_schema_graph(CONFIG_DIR)
    adapter = OracleDDLAdapter(DDL_DIR, graph)
    adapter.connect()
    tables = {t.name.lower(): t for t in adapter.introspect_schema("provider")}
    table = tables.get(table_name.lower())
    if not table:
        available = ", ".join(sorted(tables)[:10])
        raise SystemExit(f"Unknown table '{table_name}'. First few available: {available}, ...")
    return table


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def cmd_detect_sensitive(args: argparse.Namespace) -> None:
    table = _load_table(args.table)
    explicit = load_sensitive_columns(CONFIG_DIR)
    classifier = SensitivityClassifier(sensitive_columns=explicit)
    sensitive_cols = classifier.classify_table(table)
    print(f"Sensitive columns for {table.name} ({len(sensitive_cols)} of {len(table.columns)}):")
    for col in sensitive_cols:
        reason = "explicit config flag" if col.lower() in explicit else "name heuristic"
        print(f"  - {col}  [{reason}]")


def cmd_validate(args: argparse.Namespace) -> None:
    table = _load_table(args.table)
    rows = _read_csv(Path(args.input))
    glossary = load_default_glossary(CONFIG_DIR)
    graph = load_default_schema_graph(CONFIG_DIR)

    engine = ValidationEngine(ValidationEngine.default_rules_for(table))
    context = ValidationContext(schema_graph=graph, glossary=glossary)
    report = engine.validate_batch(rows, table, context)

    print(report.human_readable())
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    **report.summary(),
                    "issues": [
                        {
                            "rule": i.rule, "severity": i.severity.value, "row": i.row_index,
                            "column": i.column, "message": i.message,
                        }
                        for i in report.issues
                    ],
                },
                indent=2,
            )
        )
        print(f"\nWrote JSON report to {args.json_out}")

    sys.exit(0 if report.is_valid else 1)


def _load_ruleset(path: Path) -> MaskingPolicy:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    policy = MaskingPolicy(name=data.get("policy_name", path.stem))
    for r in data.get("rules", []):
        policy.set_rule(
            MaskingRule(
                column=r["column"],
                strategy=MaskStrategy(r["strategy"]),
                salt=r["salt"],
                faker_provider=r.get("faker_provider"),
                static_value=r.get("static_value"),
            )
        )
    return policy


def cmd_mask(args: argparse.Namespace) -> None:
    table = _load_table(args.table)
    rows = _read_csv(Path(args.input))
    policy = _load_ruleset(Path(args.ruleset))
    engine = MaskingEngine()

    explicit = load_sensitive_columns(CONFIG_DIR)
    classifier = SensitivityClassifier(sensitive_columns=explicit)
    coverage = masking_coverage(table, policy, classifier)
    print(f"Masking-coverage for {table.name}: {coverage:.0%} of classified-sensitive columns")

    if args.dry_run:
        preview = engine.apply_masking_dry_run(rows, policy, limit=args.limit)
        print(json.dumps(preview, indent=2, default=str))
        return

    masked = engine.apply_masking(rows, policy)
    out_path = Path(args.output) if args.output else Path(args.input).with_suffix(".masked.csv")
    _write_csv(out_path, masked)
    print(f"Wrote {len(masked)} masked row(s) to {out_path}")


def cmd_fk_check(args: argparse.Namespace) -> None:
    """Loads {table: ruleset.yaml} pairs from a manifest and verifies FK-linked
    columns share the same strategy/salt (Section 5's Masking/Validation
    cross-check, standing in for the not-yet-built agent pair)."""
    with open(args.manifest, "r", encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)

    engine = MaskingEngine()
    tables_and_policies = []
    for table_name, ruleset_path in manifest.items():
        table = _load_table(table_name)
        policy = _load_ruleset(Path(ruleset_path))
        tables_and_policies.append((table, policy))

    problems = engine.verify_fk_consistency(tables_and_policies)
    if problems:
        print(f"{len(problems)} FK-masking inconsistency(ies) found:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("FK-masking consistency check passed: no divergent strategy/salt across FK links.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2: Validation + Masking CLI (no agent).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect-sensitive", help="List sensitive columns for a table.")
    p_detect.add_argument("--table", required=True)
    p_detect.set_defaults(func=cmd_detect_sensitive)

    p_validate = sub.add_parser("validate", help="Validate a CSV batch against a table's structural rules.")
    p_validate.add_argument("--table", required=True)
    p_validate.add_argument("--input", required=True)
    p_validate.add_argument("--json-out")
    p_validate.set_defaults(func=cmd_validate)

    p_mask = sub.add_parser("mask", help="Apply a masking ruleset (YAML) to a CSV batch.")
    p_mask.add_argument("--table", required=True)
    p_mask.add_argument("--input", required=True)
    p_mask.add_argument("--ruleset", required=True)
    p_mask.add_argument("--output")
    p_mask.add_argument("--dry-run", action="store_true")
    p_mask.add_argument("--limit", type=int, default=20)
    p_mask.set_defaults(func=cmd_mask)

    p_fk = sub.add_parser("fk-check", help="Verify FK-linked columns share the same masking strategy/salt.")
    p_fk.add_argument("--manifest", required=True, help="YAML file: {table_name: ruleset_path.yaml}")
    p_fk.set_defaults(func=cmd_fk_check)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
