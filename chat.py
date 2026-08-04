#!/usr/bin/env python3
"""
chat.py — Phase 3 interactive entrypoint for the Manager agent.

Section 9 Phase 3: "one agent (the Manager, no subagents yet) that can
plan/explain/preview/confirm/execute for a single-table job, using local
open-weight model via Ollama by default."

Usage:
  python chat.py --input samples/p_alt_id_tb.csv
  # then type things like:
  #   validate p_alt_id_tb
  #   mask sensitive columns in p_alt_id_tb
  #   mask everything          <- deliberately ambiguous, watch it ask back

Env vars (Section 8.1):
  LLM_PROVIDER=ollama          (default; set to 'claude' to opt in)
  LLM_BASE_URL=http://localhost:11434/v1
  LLM_MODEL=qwen2:7b
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory
from core.audit import AuditLog
from core.llm_provider import load_default_provider

ROOT = Path(__file__).resolve().parent


def _ask(prompt_text: str) -> bool:
    print("\n" + prompt_text)
    reply = input("\nProceed? [y/N]: ").strip().lower()
    return reply in ("y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 Manager agent chat REPL.")
    parser.add_argument("--input", required=True, help="CSV file the current session operates on.")
    parser.add_argument("--output", help="Where to write masked output (mask jobs only).")
    args = parser.parse_args()

    llm = load_default_provider()
    print(f"Using LLM provider: {llm.provider_name} (model={llm.model})")

    manager = ManagerAgent(
        llm=llm,
        config_dir=ROOT / "config",
        ddl_dir=ROOT / "input" / "ddl",
        plan_memory=PlanMemory(ROOT / "output" / "plans"),
        audit_log=AuditLog(ROOT / "output" / "logs" / "audit.jsonl"),
    )

    print("Manager agent ready. Type a request (or 'quit').\n")
    while True:
        try:
            nl_request = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not nl_request or nl_request.lower() in ("quit", "exit"):
            break

        plan = manager.plan(nl_request)

        if plan.intent == "unclear":
            print(manager.explain(plan))
            continue

        print(manager.explain(plan))
        preview = manager.preview(plan, Path(args.input))
        print("\n--- Preview ---")
        print(preview["validation_text"])
        if plan.intent == "mask":
            print(f"Masking coverage: {preview['masking_coverage']:.0%}")
            print(f"Sensitive columns: {', '.join(preview['sensitive_columns']) or '(none detected)'}")

        confirmed = manager.request_human_confirmation(plan, ask=_ask)
        if not confirmed:
            print("Rejected. Nothing was executed.\n")
            continue

        result = manager.execute(
            plan, Path(args.input), Path(args.output) if args.output else None,
            ruleset=preview.get("_policy"),
        )
        print("\n--- Report ---")
        print(manager.report(plan.plan_id))
        print()


if __name__ == "__main__":
    sys.exit(main())
