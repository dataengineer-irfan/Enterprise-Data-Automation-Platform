"""
agent/subagents/execution_report_agent.py — Section 2.1's Execution/Report Agent.

Tools it owns: `execute_sql`, `stream_progress`, `rollback`, `write_audit_record`.

"Only runs after explicit human confirmation via the Manager; executes in
batches with rollback, streams progress, writes the audit/execution
report." This is the enforcement point for rule #2 ("Only the Execution
Agent, invoked only by the Manager, may touch the database with a write.
No other agent has write credentials") — enforced two ways, not one:

  1. Structurally: no other subagent in this package holds a `DatabasePort`
     reference at all. SQLGenerationAgent produces text; it can't execute
     anything even if asked to.
  2. At the call boundary: `execute_sql` requires the CONFIRMED plan to be
     re-read from persistent `PlanMemory` (the same pattern `ManagerAgent`
     already uses in Phase 3) rather than trusting an in-memory flag a
     caller passes in — so a bug that calls this method too early fails
     loudly (`PermissionError`) instead of silently writing.

Underneath this, `adapter.execute()` (Phase 1's `DatabasePort`) is its own
independent enforcement layer for `is_source_only` connections — so a
PROD-tier source connection refuses writes even if every layer above it
had a bug. Three independent checks for one non-negotiable rule.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from agent.plan_memory import PlanMemory, PlanStatus
from agent.subagents.base import Subagent, SubagentResult
from core.actor_context import get_effective_actor
from core.audit import AuditLog
from core.database_port import DatabasePort


class ExecutionReportAgent(Subagent):
    name = "execution_report_agent"

    def __init__(self, storage, plan_memory: PlanMemory, audit_log: AuditLog, actor: str = "execution-report-agent") -> None:
        super().__init__(storage)
        self._plans = plan_memory
        self._audit = audit_log
        self._actor = actor

    def _effective_actor(self) -> str:
        """Same per-request contextvar pattern as ManagerAgent (see
        core/actor_context.py) — the real authenticated user who confirmed
        the plan should be the one attributed for the actual write, not a
        fixed "execution-report-agent" string, once auth exists (Phase 5's
        auth pass)."""
        return get_effective_actor(self._actor)

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "execute_sql":
            return self.execute_sql(task["plan_id"], task["statements"], task["adapter"], task.get("on_progress"))
        if action == "rollback":
            return self.rollback(task["plan_id"], task["rollback_statements"], task["adapter"])
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    def execute_sql(
        self, plan_id: str, statements: list[str], adapter: DatabasePort,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> SubagentResult:
        plan = self._plans.read(plan_id)
        # EXECUTING is accepted alongside CONFIRMED: the Manager transitions
        # a plan CONFIRMED -> EXECUTING itself right before dispatching here
        # (so a mid-run crash leaves a durable "was executing" record rather
        # than looking merely "confirmed and idle"). What this gate actually
        # refuses is everything that was NEVER confirmed at all — PLANNING,
        # AWAITING_CONFIRMATION, REJECTED — which is rule #2's real intent.
        if not plan or plan.status not in (PlanStatus.CONFIRMED, PlanStatus.EXECUTING):
            self._audit.record(self._effective_actor(), "execute_sql", f"plan:{plan_id}", result="failure",
                                detail={"reason": "plan not confirmed", "status": plan.status.value if plan else None})
            raise PermissionError(
                f"Refusing to execute: plan {plan_id} was never CONFIRMED in persistent plan memory. "
                "The Execution/Report Agent re-reads plan status from storage rather than trusting "
                "a caller-supplied flag (rule #2's enforcement point)."
            )

        succeeded, failed = [], []
        for i, stmt in enumerate(statements):
            result = adapter.execute(stmt)
            if result.success:
                succeeded.append(stmt)
            else:
                failed.append({"statement": stmt, "error": result.error})
            if on_progress:
                on_progress(i + 1, len(statements))

        detail = {"succeeded": succeeded, "failed": failed}
        stored = self._store_detail("execute_sql", detail, record_count=len(statements))
        stored.success = len(failed) == 0
        stored.summary = {"plan_id": plan_id, "attempted": len(statements), "succeeded": len(succeeded), "failed": len(failed)}
        if failed:
            stored.error = f"{len(failed)} of {len(statements)} statement(s) failed."

        self._audit.record(
            self._effective_actor(), "execute_sql", f"plan:{plan_id}",
            result="success" if stored.success else "failure", detail=stored.summary,
        )
        self.write_audit_record(plan_id, "execute_sql", stored.summary)
        return stored

    def rollback(self, plan_id: str, rollback_statements: list[str], adapter: DatabasePort) -> SubagentResult:
        succeeded, failed = [], []
        for stmt in rollback_statements:
            result = adapter.execute(stmt)
            (succeeded if result.success else failed).append(stmt)

        stored = self._store_detail("rollback", {"succeeded": succeeded, "failed": failed}, record_count=len(rollback_statements))
        stored.success = len(failed) == 0
        stored.summary = {"plan_id": plan_id, "attempted": len(rollback_statements), "succeeded": len(succeeded), "failed": len(failed)}
        self._audit.record(self._effective_actor(), "rollback", f"plan:{plan_id}",
                            result="success" if stored.success else "failure", detail=stored.summary)
        self.write_audit_record(plan_id, "rollback", stored.summary)
        return stored

    def write_audit_record(self, plan_id: str, action: str, detail: dict) -> None:
        self._audit.record(self._effective_actor(), f"report:{action}", f"plan:{plan_id}", detail=detail)

    @staticmethod
    def stream_progress(current: int, total: int) -> str:
        """Default `on_progress` callback for CLI/REPL use; a UI (Phase 5)
        replaces this with a websocket/SSE push using the same signature."""
        return f"{current}/{total} statements executed ({current / total:.0%})"
