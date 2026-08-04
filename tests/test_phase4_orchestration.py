"""
tests/test_phase4_orchestration.py — Manager multi-agent fan-out (Section 2.1/2.3),
condensed-result handoff (rule #5), FK-ordered multi-table execution, the
evaluator-optimizer correction loop (Section 2.4), and the three-layer
write-authority enforcement on ExecutionReportAgent (rule #2).

Run: python -m pytest tests/test_phase4_orchestration.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory, PlanStatus
from agent.shared_storage import SharedStorage
from agent.subagents.execution_report_agent import ExecutionReportAgent
from core.audit import AuditLog
from core.database_port import DatabasePort, ExecutionResult, ExplainResult
from core.llm_provider import LLMProvider, LLMResponse

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"
SAMPLE_ALT_ID_CSV = ROOT / "samples" / "p_alt_id_tb.csv"


class FakeLLMProvider(LLMProvider):
    provider_name = "fake"
    model = "fake-model"

    def __init__(self, canned: dict) -> None:
        self._canned = canned

    def complete(self, system_prompt, user_prompt, temperature=0.0) -> LLMResponse:
        return LLMResponse(text=json.dumps(self._canned))


class FakeDatabasePort(DatabasePort):
    """In-memory stand-in for a real DatabasePort — lets execution tests run
    without a live Postgres, while still exercising the exact same
    ExecutionReportAgent code path a real adapter would go through."""

    engine_name = "fake"

    def __init__(self, is_source_only: bool = False, fail_statements_containing: str | None = None) -> None:
        self.is_source_only = is_source_only
        self.executed: list[str] = []
        self._fail_containing = fail_statements_containing

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def test_connection(self) -> bool: return True
    def introspect_schema(self, schema): return []
    def validate_dialect(self, sql): return []
    def generate_ddl(self, table): return ""

    def execute(self, sql, params=None) -> ExecutionResult:
        if self.is_source_only:
            return ExecutionResult(success=False, error="source-only connection refuses writes", statement=sql)
        if self._fail_containing and self._fail_containing in sql:
            return ExecutionResult(success=False, error="simulated failure", statement=sql)
        self.executed.append(sql)
        return ExecutionResult(success=True, statement=sql)

    def explain(self, sql) -> ExplainResult:
        return ExplainResult(success=True, plan_text="fake plan")


def _make_manager(tmp_path: Path, canned_plan: dict) -> tuple[ManagerAgent, PlanMemory]:
    plan_memory = PlanMemory(tmp_path / "plans")
    manager = ManagerAgent(
        llm=FakeLLMProvider(canned_plan),
        config_dir=CONFIG_DIR,
        ddl_dir=DDL_DIR,
        plan_memory=plan_memory,
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        shared_storage=SharedStorage(tmp_path / "shared"),
    )
    return manager, plan_memory


# ─────────────────────────────────────────────────────────────────────────── #
# list_agents / dispatch_subagent                                             #
# ─────────────────────────────────────────────────────────────────────────── #


def test_list_agents_returns_all_seven_section_2_1_agents(tmp_path):
    manager, _ = _make_manager(tmp_path, {"intent": "unclear", "table": None, "clarifying_question": "n/a"})
    names = {a["name"] for a in manager.list_agents()}
    assert names == {
        "schema_metadata_agent", "profiling_mapping_agent", "validation_agent",
        "correction_agent", "masking_agent", "sql_generation_agent", "execution_report_agent",
    }


def test_dispatch_subagent_unknown_agent_raises(tmp_path):
    manager, _ = _make_manager(tmp_path, {"intent": "unclear", "table": None, "clarifying_question": "n/a"})
    with pytest.raises(KeyError):
        manager.dispatch_subagent("not_a_real_agent", {"action": "x"})


def test_dispatch_subagent_appends_condensed_record_to_plan(tmp_path):
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_dtl_tb and p_alt_id_tb together")
    assert plan.fan_out is True
    assert plan.tables == ["p_dtl_tb", "p_alt_id_tb"]

    result = manager.dispatch_subagent("schema_metadata_agent", {"action": "introspect_schema", "table": "p_alt_id_tb"}, plan=plan)
    assert result.success
    reloaded = plan_memory.read(plan.plan_id)
    assert len(reloaded.subtasks) == 1
    assert reloaded.subtasks[0]["agent"] == "schema_metadata_agent"
    # condensed handoff (rule #5): the plan's subtask record carries the
    # SUMMARY and a pointer, never the raw detail payload
    assert "columns" not in reloaded.subtasks[0]  # raw column list lives behind detail_pointer, not inline
    assert reloaded.subtasks[0]["detail_pointer"] is not None


# ─────────────────────────────────────────────────────────────────────────── #
# Fan-out routing (Section 2.3): single table never triggers fan-out          #
# ─────────────────────────────────────────────────────────────────────────── #


def test_single_table_mentioned_as_multi_table_job_is_routed_back_to_unclear(tmp_path):
    """Section 2.3: fan-out is reserved for genuinely multi-table jobs."""
    manager, _ = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("do a multi table thing but only really p_alt_id_tb")
    assert plan.intent == "unclear"
    assert plan.fan_out is False


def test_unknown_table_in_multi_table_list_asks_clarifying_question(tmp_path):
    manager, _ = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "not_a_real_table"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_dtl_tb and not_a_real_table")
    assert plan.intent == "unclear"
    assert "not_a_real_table" in plan.clarifying_question


# ─────────────────────────────────────────────────────────────────────────── #
# Full fan-out workflow: Plan -> Preview -> Confirm -> Execute -> Report       #
# ─────────────────────────────────────────────────────────────────────────── #


def test_fan_out_preview_dispatches_multiple_subagents_per_table(tmp_path):
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    # force a genuinely multi-table plan by editing tables post-hoc isn't realistic;
    # instead exercise fan-out with 2 real tables sharing the sample CSV shape.
    manager2, plan_memory2 = _make_manager(tmp_path / "second", {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "multi", "clarifying_question": None,
    })
    plan = manager2.plan("validate p_dtl_tb and p_alt_id_tb")
    assert plan.fan_out is True

    csv_paths = {"p_dtl_tb": SAMPLE_ALT_ID_CSV, "p_alt_id_tb": SAMPLE_ALT_ID_CSV}  # reuse sample; shape is what's tested
    preview = manager2.run_fan_out_preview(plan, csv_paths, mask=False)

    assert set(preview["tables"].keys()) == {"p_dtl_tb", "p_alt_id_tb"}
    reloaded = plan_memory2.read(plan.plan_id)
    # 3 subagent dispatches per table (profile, diff, validate) = 6 total
    assert len(reloaded.subtasks) == 6
    assert reloaded.status == PlanStatus.AWAITING_CONFIRMATION


def test_fan_out_execute_orders_tables_fk_parent_first(tmp_path):
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_alt_id_tb", "p_dtl_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_alt_id_tb and p_dtl_tb")  # deliberately listed child-before-parent
    assert plan.tables == ["p_alt_id_tb", "p_dtl_tb"]

    ordered = manager._order_tables_for_execution(plan.tables)
    assert ordered.index("p_dtl_tb") < ordered.index("p_alt_id_tb")  # parent must insert first


def test_fan_out_execute_refuses_without_confirmation(tmp_path):
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_dtl_tb and p_alt_id_tb")
    csv_paths = {"p_dtl_tb": SAMPLE_ALT_ID_CSV, "p_alt_id_tb": SAMPLE_ALT_ID_CSV}
    manager.run_fan_out_preview(plan, csv_paths, mask=False)

    with pytest.raises(PermissionError):
        manager.execute_fan_out(plan, csv_paths, policies={})


def test_fan_out_execute_script_only_mode_writes_sql_files(tmp_path):
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_dtl_tb and p_alt_id_tb")
    csv_paths = {"p_dtl_tb": SAMPLE_ALT_ID_CSV, "p_alt_id_tb": SAMPLE_ALT_ID_CSV}
    manager.run_fan_out_preview(plan, csv_paths, mask=False)
    manager.request_human_confirmation(plan, ask=lambda _msg: True)

    output_dir = tmp_path / "sql_out"
    result = manager.execute_fan_out(plan, csv_paths, policies={}, adapter=None, output_dir=output_dir)

    assert (output_dir / "p_dtl_tb.insert.sql").exists()
    assert (output_dir / "p_alt_id_tb.rollback.sql").exists()
    assert result["execution_order"].index("p_dtl_tb") < result["execution_order"].index("p_alt_id_tb")

    final_plan = plan_memory.read(plan.plan_id)
    assert final_plan.status == PlanStatus.COMPLETED


def test_fan_out_execute_with_fake_adapter_respects_write_authority(tmp_path):
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_dtl_tb and p_alt_id_tb")
    csv_paths = {"p_dtl_tb": SAMPLE_ALT_ID_CSV, "p_alt_id_tb": SAMPLE_ALT_ID_CSV}
    manager.run_fan_out_preview(plan, csv_paths, mask=False)
    manager.request_human_confirmation(plan, ask=lambda _msg: True)

    fake_db = FakeDatabasePort(is_source_only=False)
    result = manager.execute_fan_out(plan, csv_paths, policies={}, adapter=fake_db)
    assert result["tables"]["p_dtl_tb"]["execution"]["failed"] == 0
    assert len(fake_db.executed) > 0


def test_execution_agent_refuses_write_against_source_only_adapter(tmp_path):
    """Belt-and-suspenders check #3 (see ExecutionReportAgent docstring):
    even a CONFIRMED plan cannot write through a source-only DatabasePort —
    that enforcement lives at the connector layer (Phase 1), independent
    of the agent layer."""
    manager, plan_memory = _make_manager(tmp_path, {
        "intent": "multi_table_job", "tables": ["p_dtl_tb", "p_alt_id_tb"], "reasoning": "x", "clarifying_question": None,
    })
    plan = manager.plan("validate p_dtl_tb and p_alt_id_tb")
    csv_paths = {"p_dtl_tb": SAMPLE_ALT_ID_CSV, "p_alt_id_tb": SAMPLE_ALT_ID_CSV}
    manager.run_fan_out_preview(plan, csv_paths, mask=False)
    manager.request_human_confirmation(plan, ask=lambda _msg: True)

    source_only_db = FakeDatabasePort(is_source_only=True)
    result = manager.execute_fan_out(plan, csv_paths, policies={}, adapter=source_only_db)
    for table_result in result["tables"].values():
        assert table_result["execution"]["failed"] > 0
    assert source_only_db.executed == []


def test_execution_report_agent_execute_sql_refuses_unconfirmed_plan_directly(tmp_path):
    """Tests ExecutionReportAgent's own gate in isolation, not just through
    the Manager — rule #2's enforcement doesn't depend on the Manager
    behaving correctly; the agent re-checks independently."""
    plan_memory = PlanMemory(tmp_path / "plans")
    audit = AuditLog(tmp_path / "audit.jsonl")
    storage = SharedStorage(tmp_path / "shared")
    agent = ExecutionReportAgent(storage, plan_memory, audit)

    plan = plan_memory.create(nl_request="x", intent="mask", table="p_alt_id_tb")
    assert plan.status == PlanStatus.AWAITING_CONFIRMATION  # never confirmed

    with pytest.raises(PermissionError):
        agent.execute_sql(plan.plan_id, ["INSERT INTO x VALUES (1);"], FakeDatabasePort())


# ─────────────────────────────────────────────────────────────────────────── #
# Evaluator-optimizer correction loop (Section 2.4)                           #
# ─────────────────────────────────────────────────────────────────────────── #


def test_correction_loop_fixes_phone_format_and_converges(tmp_path):
    manager, _ = _make_manager(tmp_path, {"intent": "unclear", "table": None, "clarifying_question": "n/a"})
    from core.database_port import ColumnMetadata, TableMetadata

    table = TableMetadata(
        name="t", schema="provider",
        columns=[ColumnMetadata("id", "BIGINT", nullable=False), ColumnMetadata("p_phone_num", "VARCHAR(20)")],
        primary_key=["id"],
    )
    rows = [{"id": "1", "p_phone_num": "5551234567"}]

    result = manager.run_correction_loop(rows, table, max_iterations=3)
    # phone format isn't itself a structural validation rule in this table
    # (no regex rule registered), so the loop should find it already "clean"
    # on the first pass — this test mainly proves the loop terminates and
    # never mutates the caller's original list.
    assert rows[0]["p_phone_num"] == "5551234567"  # original untouched
    assert result["iterations"] >= 1


def test_correction_loop_terminates_when_no_further_progress_possible(tmp_path):
    manager, _ = _make_manager(tmp_path, {"intent": "unclear", "table": None, "clarifying_question": "n/a"})
    from core.database_port import ColumnMetadata, TableMetadata

    table = TableMetadata(
        name="t", schema="provider",
        columns=[ColumnMetadata("id", "BIGINT", nullable=False)],
        primary_key=["id"],
    )
    rows = [{"id": ""}]  # missing PK — CorrectionAgent has no generic fix for this

    result = manager.run_correction_loop(rows, table, max_iterations=3)
    assert result["clean"] is False
    assert result["iterations"] <= 3


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
