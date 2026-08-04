"""
agent/manager.py — Manager Agent (Section 2.1).

Phase 3 gave it a single linear workflow for one-table jobs. Phase 4 adds
the specialized subagents from Section 2.1 plus real fan-out: `plan()` now
also recognizes multi-table requests, and `run_fan_out_preview()` /
`execute_fan_out()` dispatch to the Schema/Metadata, Profiling/Mapping,
Validation, Masking, SQL Generation, and Execution/Report subagents —
reserved for jobs Section 2.3 says a single agent genuinely can't do well
(multi-table, multi-source, ambiguous mapping). A plain single-table
request still takes the Phase 3 linear path unchanged; fan-out is opt-in
by job shape, not a replacement for it (Section 2.3: "the Manager should
run it as a linear workflow directly, without spinning up the full
subagent roster").

Implements the non-negotiable Section 2.2 rule #1 workflow — Plan ->
Explain -> Preview -> Confirm -> Execute -> Report — for both paths; no
agent may skip from "plan" to "execute" (rule #1), only a CONFIRMED plan
(re-read from persistent storage, never a caller-supplied flag) may be
executed (rule #2), every tool call/handoff/confirmation is audited
(rule #3), ambiguity always produces a clarifying question (rule #4),
subagents hand back condensed summaries + a shared-storage pointer rather
than raw data dumps (rule #5), and the plan itself is durable, not
in-context, so a job survives a restart mid-way (rule #6).

`list_agents()` / `dispatch_subagent()` are real now — every subtask
dispatch is recorded to both the audit log and the plan's `subtasks` list.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Callable, Optional

from adapters.oracle_ddl_adapter import OracleDDLAdapter
from agent.plan_memory import Plan, PlanMemory, PlanStatus
from core.actor_context import get_effective_actor
from agent.shared_storage import SharedStorage
from agent.subagents.base import Subagent, SubagentResult
from agent.subagents.correction_agent import CorrectionAgent
from agent.subagents.execution_report_agent import ExecutionReportAgent
from agent.subagents.masking_agent import MaskingAgent
from agent.subagents.profiling_mapping_agent import ProfilingMappingAgent
from agent.subagents.schema_metadata_agent import SchemaMetadataAgent
from agent.subagents.sql_generation_agent import SQLGenerationAgent
from agent.subagents.validation_agent import ValidationAgent
from core.audit import AuditLog
from core.database_port import DatabasePort, TableMetadata
from core.glossary import load_default_glossary
from core.llm_provider import LLMProvider, extract_json_object
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
from core.validation import ValidationContext, ValidationEngine

logger = logging.getLogger(__name__)

_VALID_INTENTS = {"validate", "mask", "schema_question", "multi_table_job", "unclear"}

_SYSTEM_PROMPT = """You are the planning component of a data-platform Manager agent.
You NEVER execute anything yourself — you only classify the user's request and
respond with a single JSON object, nothing else (no markdown fences, no commentary).

Schema:
{
  "intent": "validate" | "mask" | "schema_question" | "multi_table_job" | "unclear",
  "table": "<lowercase table name mentioned, or null>",
  "tables": ["<lowercase table name>", "..."] or null,
  "reasoning": "<one sentence>",
  "clarifying_question": "<a question to ask the user, or null>"
}

Rules:
- If the request does not name a specific table, or the intent is genuinely
  ambiguous (e.g. "clean up the data", "mask everything"), set intent to
  "unclear" and ALWAYS provide a clarifying_question. Never guess a
  destructive default.
- "mask" means the user wants sensitive columns de-identified in ONE table.
- "validate" means the user wants data-quality checks run, no masking, in ONE table.
- "multi_table_job" means the user named TWO OR MORE tables, or asked for
  something spanning multiple tables (e.g. "load and validate p_dtl_tb and
  p_alt_id_tb together"). Use the "tables" list, not "table", for this intent.
- Respond with ONLY the JSON object.
"""


class ManagerAgent:
    def __init__(
        self,
        llm: LLMProvider,
        config_dir: Path,
        ddl_dir: Path,
        plan_memory: PlanMemory,
        audit_log: AuditLog,
        shared_storage: Optional[SharedStorage] = None,
        actor: str = "manager-agent",
    ) -> None:
        self._llm = llm
        self._plans = plan_memory
        self._audit = audit_log
        self._actor = actor

        self._graph = load_default_schema_graph(config_dir)
        self._glossary = load_default_glossary(config_dir)
        self._adapter = OracleDDLAdapter(ddl_dir, self._graph)
        self._adapter.connect()
        self._tables = {t.name.lower(): t for t in self._adapter.introspect_schema("provider")}
        self._sensitive_columns = load_sensitive_columns(config_dir)

        self._storage = shared_storage or SharedStorage(config_dir.parent / "output" / "shared_storage")
        self._subagents: dict[str, Subagent] = {
            "schema_metadata_agent": SchemaMetadataAgent(self._storage, self._adapter, self._graph, self._glossary),
            "profiling_mapping_agent": ProfilingMappingAgent(self._storage),
            "validation_agent": ValidationAgent(self._storage, self._graph, self._glossary),
            "correction_agent": CorrectionAgent(self._storage),
            "masking_agent": MaskingAgent(self._storage, self._sensitive_columns),
            "sql_generation_agent": SQLGenerationAgent(self._storage),
            "execution_report_agent": ExecutionReportAgent(self._storage, self._plans, self._audit, actor=f"{actor}:execution-report-agent"),
        }

    # -- Public read accessors (for API/UI layers — avoids reaching into _tables/_storage directly) --
    def get_table(self, name: str):
        return self._tables.get(name.lower())

    def list_table_names(self) -> list[str]:
        return sorted(self._tables)

    @property
    def storage(self) -> SharedStorage:
        return self._storage

    @property
    def plan_memory(self) -> PlanMemory:
        return self._plans

    @property
    def audit_log(self) -> AuditLog:
        return self._audit

    def order_tables_for_execution(self, tables: list[str]) -> list[str]:
        """Public wrapper around the FK-safe topological ordering used
        internally by execute_fan_out() — exposed so callers (e.g. Job
        Monitor's API) can display execution order without duplicating the
        graph-sort logic or reaching into a private method."""
        return self._order_tables_for_execution(tables)

    # -- Manager tools: list_agents / dispatch_subagent ------------------------
    def list_agents(self) -> list[dict]:
        """Section 2.1's `list_agents` tool."""
        return [{"name": name, "class": type(agent).__name__} for name, agent in self._subagents.items()]

    def dispatch_subagent(self, agent_name: str, task: dict, plan: Optional[Plan] = None) -> SubagentResult:
        """Section 2.1's `dispatch_subagent` tool — the ONLY way the Manager
        talks to a subagent, and subagents never talk to each other
        directly (Section 2.1). Every dispatch is audited (rule #3) and, if
        a `plan` is supplied, appended to that plan's persisted `subtasks`
        list as a condensed record (rule #5/#6) — never the raw detail."""
        agent = self._subagents.get(agent_name)
        if not agent:
            raise KeyError(f"Unknown subagent: {agent_name!r}. Known agents: {sorted(self._subagents)}")

        result = agent.run(task)
        self._audit.record(
            self._effective_actor(), "dispatch_subagent", f"subagent:{agent_name}",
            result="success" if result.success else "failure",
            detail={"action": task.get("action"), **result.to_dict()},
        )
        if plan:
            # Read-modify-write against the PERSISTED plan, not the
            # possibly-stale object the caller is holding: update_status()
            # calls elsewhere (confirm, execute) write through PlanMemory
            # and return a NEW Plan instance without mutating the caller's
            # reference, so writing `plan` here as-is could silently clobber
            # a status transition (e.g. CONFIRMED) that happened after this
            # `plan` object was captured. Merging fresh-from-disk fields back
            # into the caller's object keeps both consistent going forward.
            fresh = self._plans.read(plan.plan_id) or plan
            fresh.subtasks.append(result.to_dict())
            self._plans.write(fresh)
            plan.__dict__.update(fresh.__dict__)
        return result

    @staticmethod
    def _sync(plan: Plan, updated: Plan) -> None:
        """Keeps a caller-held Plan object's in-memory state consistent with
        whatever PlanMemory.update_status() just persisted — update_status()
        returns a new instance rather than mutating in place, so every call
        site that keeps using its local `plan` variable afterward needs this,
        not just dispatch_subagent() (see its comment for the bug this fixes)."""
        plan.__dict__.update(updated.__dict__)

    def _effective_actor(self) -> str:
        """Real authenticated username for the current request if one is
        set (see core/actor_context.py), else this instance's constructor
        default (e.g. "manager-agent" for CLI/REPL usage, which never runs
        inside a request context and has no per-request user to attribute
        to)."""
        return get_effective_actor(self._actor)

    # -- Step 1: Plan --------------------------------------------------------
    def plan(self, nl_request: str) -> Plan:
        response = self._llm.complete(_SYSTEM_PROMPT, nl_request)
        self._audit.record(
            self._effective_actor(), "llm_plan_request", "manager",
            result="success" if response.success else "failure",
            detail={"nl_request": nl_request, "provider": self._llm.provider_name, "model": self._llm.model},
        )

        if not response.success:
            return self._plans.create(
                nl_request=nl_request, intent="unclear",
                reasoning="LLM provider unavailable.",
                clarifying_question=(
                    f"I couldn't reach the {self._llm.provider_name} model ({response.error}). "
                    "Please retry once it's available, or specify the table and action directly."
                ),
            )

        parsed = extract_json_object(response.text)
        if not parsed or parsed.get("intent") not in _VALID_INTENTS:
            return self._plans.create(
                nl_request=nl_request, intent="unclear",
                reasoning="Could not parse a valid plan from the model's response.",
                clarifying_question="I didn't understand that request. Could you name the table and say 'validate' or 'mask'?",
            )

        table = (parsed.get("table") or "").strip().lower() or None
        raw_tables = parsed.get("tables") or []
        tables = [t.strip().lower() for t in raw_tables if isinstance(t, str) and t.strip()]

        if table and table not in self._tables:
            sample = ", ".join(sorted(self._tables)[:8])
            return self._plans.create(
                nl_request=nl_request, intent="unclear",
                reasoning=f"Table '{table}' not found in schema.",
                clarifying_question=f"I don't see a table called '{table}' in the Provider schema. "
                                     f"Did you mean one of: {sample}, ...?",
            )

        unknown_tables = [t for t in tables if t not in self._tables]
        if unknown_tables:
            sample = ", ".join(sorted(self._tables)[:8])
            return self._plans.create(
                nl_request=nl_request, intent="unclear",
                reasoning=f"Table(s) {unknown_tables} not found in schema.",
                clarifying_question=f"I don't recognize: {', '.join(unknown_tables)}. "
                                     f"Did you mean one of: {sample}, ...?",
            )

        intent = parsed["intent"]
        clarifying_question = None
        if intent in ("validate", "mask") and not table:
            intent = "unclear"
            clarifying_question = parsed.get("clarifying_question") or "Which table should I work on?"
        elif intent == "multi_table_job" and len(tables) < 2:
            # Section 2.3: fan-out is reserved for genuinely multi-table jobs — a
            # single table here means the Manager should have said "validate"/"mask"
            # instead, so route it back to a clarifying question rather than
            # spinning up the full subagent roster for one table.
            intent = "unclear"
            clarifying_question = "That sounded like a single-table job — which one table, and validate or mask?"
        elif intent == "unclear":
            clarifying_question = parsed.get("clarifying_question") or "Could you clarify what you'd like me to do?"

        plan = self._plans.create(
            nl_request=nl_request, intent=intent, table=table, tables=tables,
            steps=self._steps_for_intent(intent), reasoning=parsed.get("reasoning", ""),
            clarifying_question=clarifying_question, fan_out=(intent == "multi_table_job"),
        )
        self._audit.record(self._effective_actor(), "plan_created", f"plan:{plan.plan_id}", detail=plan.to_dict())
        return plan

    @staticmethod
    def _steps_for_intent(intent: str) -> list[str]:
        return {
            "validate": ["introspect_schema", "validate_batch", "preview_report"],
            "mask": ["introspect_schema", "detect_sensitive", "validate_batch", "apply_masking_dry_run", "preview_report"],
            "schema_question": ["introspect_schema", "describe_table"],
            "multi_table_job": [
                "dispatch:schema_metadata_agent", "dispatch:profiling_mapping_agent",
                "dispatch:validation_agent", "dispatch:masking_agent (if requested)",
                "dispatch:sql_generation_agent", "preview_report",
            ],
        }.get(intent, [])

    # -- Step 2: Explain --------------------------------------------------------
    def explain(self, plan: Plan) -> str:
        if plan.intent == "unclear":
            return f"I need more information: {plan.clarifying_question}"
        target = f"Tables: {', '.join(plan.tables)}" if plan.fan_out else f"Table: {plan.table}"
        lines = [f'Plan for: "{plan.nl_request}"', f"  Intent: {plan.intent}", f"  {target}"]
        if plan.reasoning:
            lines.append(f"  Reasoning: {plan.reasoning}")
        lines.append("  Steps: " + " -> ".join(plan.steps))
        return "\n".join(lines)

    # -- Step 3: Preview --------------------------------------------------------
    def preview(self, plan: Plan, input_csv: Path, ruleset: Optional[MaskingPolicy] = None) -> dict:
        if plan.intent == "unclear":
            raise ValueError("Cannot preview an unclear plan; resolve the clarifying question first.")

        table = self._tables[plan.table]
        rows = self._read_csv(input_csv)

        engine = ValidationEngine(ValidationEngine.default_rules_for(table))
        context = ValidationContext(schema_graph=self._graph, glossary=self._glossary)
        report = engine.validate_batch(rows, table, context)
        result: dict = {"validation": report.summary(), "validation_text": report.human_readable()}

        policy: Optional[MaskingPolicy] = None
        if plan.intent == "mask":
            classifier = SensitivityClassifier(sensitive_columns=self._sensitive_columns)
            sensitive_cols = classifier.classify_table(table)
            policy = ruleset or self._default_masking_policy(table, sensitive_cols)
            result["sensitive_columns"] = sensitive_cols
            result["masking_coverage"] = masking_coverage(table, policy, classifier)
            result["masking_preview"] = MaskingEngine().apply_masking_dry_run(rows, policy, limit=10)

        plan.preview = {k: v for k, v in result.items()}
        self._sync(plan, self._plans.update_status(plan.plan_id, PlanStatus.AWAITING_CONFIRMATION, preview=plan.preview))
        self._audit.record(self._effective_actor(), "preview_generated", f"plan:{plan.plan_id}",
                            detail={"table": plan.table, "row_count": len(rows)})
        return {**result, "_policy": policy}

    @staticmethod
    def _default_masking_policy(table: TableMetadata, sensitive_cols: list[str]) -> MaskingPolicy:
        policy = MaskingPolicy(name=f"{table.name}_auto")
        for col in sensitive_cols:
            policy.set_rule(MaskingRule(column=col, strategy=MaskStrategy.DETERMINISTIC, salt=f"auto-{table.name}"))
        return policy

    # -- Step 4: Confirm --------------------------------------------------------
    def request_human_confirmation(self, plan: Plan, ask: Callable[[str], bool]) -> bool:
        """`ask` is injected (CLI `input()` wrapper today; a UI approval
        callback in Phase 5) so the Manager's control flow never depends on
        a particular I/O surface. This is the ONLY place a plan may move to
        CONFIRMED — execute() checks that status, not this return value,
        so a caller can't skip straight to execute()."""
        confirmed = ask(self.explain(plan))
        status = PlanStatus.CONFIRMED if confirmed else PlanStatus.REJECTED
        self._sync(plan, self._plans.update_status(plan.plan_id, status))
        self._audit.record(self._effective_actor(), "human_confirmation", f"plan:{plan.plan_id}",
                            result="success" if confirmed else "failure", detail={"confirmed": confirmed})
        return confirmed

    # -- Step 5: Execute --------------------------------------------------------
    def execute(self, plan: Plan, input_csv: Path, output_csv: Optional[Path] = None,
                ruleset: Optional[MaskingPolicy] = None) -> dict:
        fresh = self._plans.read(plan.plan_id)
        if not fresh or fresh.status != PlanStatus.CONFIRMED:
            raise PermissionError(
                "Refusing to execute: plan is not in CONFIRMED status in persistent plan "
                "memory. Only request_human_confirmation() returning True may set that "
                "status — execute() re-reads it from storage rather than trusting a "
                "caller-supplied flag, so a skipped confirmation step fails loudly."
            )
        self._sync(plan, self._plans.update_status(plan.plan_id, PlanStatus.EXECUTING))

        table = self._tables[plan.table]
        rows = self._read_csv(input_csv)

        if plan.intent == "validate":
            engine = ValidationEngine(ValidationEngine.default_rules_for(table))
            report = engine.validate_batch(rows, table, ValidationContext(schema_graph=self._graph, glossary=self._glossary))
            result = {"action": "validate", "summary": report.summary()}

        elif plan.intent == "mask":
            classifier = SensitivityClassifier(sensitive_columns=self._sensitive_columns)
            policy = ruleset or self._default_masking_policy(table, classifier.classify_table(table))
            masked_rows = MaskingEngine().apply_masking(rows, policy)
            out_path = output_csv or input_csv.with_suffix(".masked.csv")
            self._write_csv(out_path, masked_rows)
            result = {"action": "mask", "output_path": str(out_path), "rows_masked": len(masked_rows)}

        else:
            result = {"action": plan.intent, "note": "no data-mutating operation for this intent"}

        self._sync(plan, self._plans.update_status(plan.plan_id, PlanStatus.COMPLETED, result=result))
        self._audit.record(self._effective_actor(), "execute", f"plan:{plan.plan_id}", detail=result)
        return result

    # -- Fan-out path (Phase 4): Preview across multiple tables --------------------------------------------------------
    def run_fan_out_preview(self, plan: Plan, csv_paths: dict[str, Path], mask: bool = False) -> dict:
        """Multi-table equivalent of `preview()`. For each table in
        `plan.tables`, dispatches Profiling/Mapping -> Schema/Metadata ->
        Validation -> (optionally) Masking, all via `dispatch_subagent` so
        every step is audited and condensed into `plan.subtasks`. Returns a
        per-table summary dict; nothing here writes anything."""
        if not plan.fan_out:
            raise ValueError("run_fan_out_preview() is only for fan_out plans; use preview() for single-table jobs.")
        missing = [t for t in plan.tables if t not in csv_paths]
        if missing:
            raise ValueError(f"No CSV path supplied for table(s): {missing}")

        per_table: dict[str, dict] = {}
        policies: dict[str, MaskingPolicy] = {}

        for table_name in plan.tables:
            table = self._tables[table_name]
            csv_path = csv_paths[table_name]

            profile_result = self.dispatch_subagent(
                "profiling_mapping_agent", {"action": "profile_file", "path": str(csv_path)}, plan=plan,
            )
            diff_result = self.dispatch_subagent(
                "profiling_mapping_agent",
                {"action": "diff_against_schema", "profile": self._storage.read_detail(profile_result.detail_pointer.pointer), "table": table},
                plan=plan,
            )

            rows = self._read_csv(csv_path)
            validate_result = self.dispatch_subagent(
                "validation_agent", {"action": "validate_batch", "rows": rows, "table": table}, plan=plan,
            )

            table_summary = {
                "profile": profile_result.summary,
                "schema_diff": diff_result.summary,
                "validation": validate_result.summary,
            }

            if mask:
                classify_result = self.dispatch_subagent(
                    "masking_agent", {"action": "classify_sensitivity", "table": table}, plan=plan,
                )
                propose_result = self.dispatch_subagent(
                    "masking_agent", {"action": "propose_masking_rule", "table": table}, plan=plan,
                )
                policy = propose_result.runtime_payload
                policies[table_name] = policy
                dry_run_result = self.dispatch_subagent(
                    "masking_agent", {"action": "apply_masking_dry_run", "rows": rows, "policy": policy}, plan=plan,
                )
                table_summary["sensitivity"] = classify_result.summary
                table_summary["masking_proposal"] = propose_result.summary
                table_summary["masking_preview"] = dry_run_result.summary

            per_table[table_name] = table_summary

        preview = {"tables": per_table, "mask": mask}
        plan.preview = preview
        self._sync(plan, self._plans.update_status(plan.plan_id, PlanStatus.AWAITING_CONFIRMATION, preview=preview))
        self._audit.record(self._effective_actor(), "fan_out_preview_generated", f"plan:{plan.plan_id}",
                            detail={"tables": plan.tables})
        return {**preview, "_policies": policies}

    def _order_tables_for_execution(self, tables: list[str]) -> list[str]:
        """Uses the Phase 1 schema graph's topological sort so a multi-table
        job inserts parents before children (e.g. p_dtl_tb before
        p_alt_id_tb) even when the user listed them in any order."""
        full_order = self._graph.topological_insert_order()
        wanted = {t.upper() for t in tables}
        ordered = [t.lower() for t in full_order if t in wanted]
        # anything not in the graph's topological order (shouldn't happen for
        # known tables, but stay defensive) goes last, stable order preserved
        leftover = [t for t in tables if t not in ordered]
        return ordered + leftover

    # -- Fan-out path (Phase 4): Execute across multiple tables --------------------------------------------------------
    def execute_fan_out(
        self, plan: Plan, csv_paths: dict[str, Path], policies: dict[str, MaskingPolicy],
        adapter: Optional[DatabasePort] = None, output_dir: Optional[Path] = None,
    ) -> dict:
        """Multi-table equivalent of `execute()`. Requires CONFIRMED status,
        re-read from persistent storage exactly like the single-table path
        (rule #2). Tables are executed in FK-safe order. If `adapter` is
        None, this runs in script-only mode (Section 6: SQL is "always
        downloadable as a script, independent of whether it's executed") —
        it generates and writes the INSERT + rollback scripts per table but
        performs no live write. If `adapter` is supplied, each table's
        generated SQL is handed to the Execution/Report Agent, which is the
        only class in this codebase holding write authority (rule #2)."""
        fresh = self._plans.read(plan.plan_id)
        if not fresh or fresh.status != PlanStatus.CONFIRMED:
            raise PermissionError(
                "Refusing to execute: fan-out plan is not CONFIRMED in persistent plan memory."
            )
        self._sync(plan, self._plans.update_status(plan.plan_id, PlanStatus.EXECUTING))

        ordered_tables = self._order_tables_for_execution(plan.tables)
        results: dict[str, dict] = {}

        for table_name in ordered_tables:
            table = self._tables[table_name]
            rows = self._read_csv(csv_paths[table_name])
            if table_name in policies:
                rows = MaskingEngine().apply_masking(rows, policies[table_name])

            sql_result = self.dispatch_subagent(
                "sql_generation_agent", {"action": "generate_sql", "table": table, "rows": rows}, plan=plan,
            )
            estimate_result = self.dispatch_subagent(
                "sql_generation_agent", {"action": "estimate_execution_time", "row_count": len(rows)}, plan=plan,
            )
            rollback_result = self.dispatch_subagent(
                "sql_generation_agent", {"action": "build_rollback_plan", "table": table, "rows": rows}, plan=plan,
            )
            statements = sql_result.runtime_payload or []

            table_result = {
                "sql_summary": sql_result.summary,
                "estimate": estimate_result.summary,
                "rollback_summary": rollback_result.summary,
            }

            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / f"{table_name}.insert.sql").write_text("\n".join(statements))
                (output_dir / f"{table_name}.rollback.sql").write_text("\n".join(rollback_result.runtime_payload or []))
                table_result["scripts_written_to"] = str(output_dir)

            if adapter is not None:
                exec_result = self.dispatch_subagent(
                    "execution_report_agent",
                    {"action": "execute_sql", "plan_id": plan.plan_id, "statements": statements, "adapter": adapter},
                    plan=plan,
                )
                table_result["execution"] = exec_result.summary

            results[table_name] = table_result

        overall_result = {"action": "multi_table_job", "tables": results, "execution_order": ordered_tables}
        self._sync(plan, self._plans.update_status(plan.plan_id, PlanStatus.COMPLETED, result=overall_result))
        self._audit.record(self._effective_actor(), "fan_out_execute", f"plan:{plan.plan_id}", detail={"tables": ordered_tables})
        return overall_result

    # -- Evaluator-optimizer loop (Section 2.4): Correction <-> Validation --------------------------------------------------------
    def run_correction_loop(self, rows: list[dict], table: TableMetadata, max_iterations: int = 3) -> dict:
        """Section 2.4: "propose fix -> Validation Agent re-checks -> loop
        until clean or max-iterations, then hand back to human." Alternates
        CorrectionAgent (propose-only, never applies) and ValidationAgent
        (report-only, never fixes) — exactly the division of responsibility
        Section 2.1 assigns each agent. Returns the corrected rows (a copy;
        the input is never mutated) plus the iteration history for audit."""
        working_rows = [dict(r) for r in rows]
        history = []

        for iteration in range(1, max_iterations + 1):
            validate_result = self.dispatch_subagent(
                "validation_agent", {"action": "validate_batch", "rows": working_rows, "table": table},
            )
            issue_detail = self._storage.read_detail(validate_result.detail_pointer.pointer)
            errors = [i for i in issue_detail["issues"] if i["severity"] == "error"]

            if not errors:
                history.append({"iteration": iteration, "remaining_errors": 0})
                return {"rows": working_rows, "clean": True, "iterations": iteration, "history": history}

            fixes_applied = 0
            for issue in errors:
                col = issue.get("column")
                row_idx = issue.get("row")
                if col is None or row_idx is None or row_idx >= len(working_rows):
                    continue
                correction_result = self.dispatch_subagent(
                    "correction_agent",
                    {"action": "suggest_correction", "column": col, "value": working_rows[row_idx].get(col), "violation_rule": issue.get("rule", "")},
                )
                if correction_result.summary.get("fixable"):
                    working_rows[row_idx][col] = correction_result.summary["after"]
                    fixes_applied += 1

            history.append({"iteration": iteration, "remaining_errors": len(errors), "fixes_applied": fixes_applied})
            if fixes_applied == 0:
                break  # no more progress possible — hand back to human rather than loop forever

        return {"rows": working_rows, "clean": False, "iterations": len(history), "history": history}

    # -- Step 6: Report --------------------------------------------------------
    def report(self, plan_id: str) -> str:
        plan = self._plans.read(plan_id)
        if not plan:
            return f"No such plan: {plan_id}"
        lines = [f"Plan {plan.plan_id} — status: {plan.status.value}", f"  Request: {plan.nl_request}"]
        if plan.result:
            lines.append(f"  Result: {json.dumps(plan.result, indent=2, default=str)}")
        return "\n".join(lines)

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _read_csv(path: Path) -> list[dict]:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    @staticmethod
    def _write_csv(path: Path, rows: list[dict]) -> None:
        if not rows:
            path.write_text("")
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
