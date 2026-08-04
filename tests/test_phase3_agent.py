"""
tests/test_phase3_agent.py — Phase 3 smoke tests.

The Manager's *planning* step depends on an LLM, but its *safety* behavior
(never skip Plan->Explain->Preview->Confirm->Execute->Report, never guess
a destructive default on ambiguity, never execute an unconfirmed plan)
must be testable without a live Ollama instance — so these tests inject a
FakeLLMProvider that returns canned responses. One test at the bottom
confirms OllamaProvider itself fails gracefully (returns a structured
error, doesn't raise) when nothing is listening on the configured port,
since that's the realistic state of this sandbox.

Run: python -m pytest tests/test_phase3_agent.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory, PlanStatus
from core.audit import AuditLog
from core.llm_provider import LLMProvider, LLMResponse, OllamaProvider

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DDL_DIR = ROOT / "input" / "ddl"
SAMPLE_CSV = ROOT / "samples" / "p_alt_id_tb.csv"


class FakeLLMProvider(LLMProvider):
    provider_name = "fake"
    model = "fake-model"

    def __init__(self, canned: dict | None = None, error: str | None = None) -> None:
        self._canned = canned
        self._error = error

    def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> LLMResponse:
        if self._error:
            return LLMResponse(text="", error=self._error)
        return LLMResponse(text=json.dumps(self._canned))


def _make_manager(llm: LLMProvider, tmp_path: Path) -> ManagerAgent:
    return ManagerAgent(
        llm=llm,
        config_dir=CONFIG_DIR,
        ddl_dir=DDL_DIR,
        plan_memory=PlanMemory(tmp_path / "plans"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
    )


# ─────────────────────────────────────────────────────────────────────────── #
# Step 1: Plan — intent classification & ambiguity handling                   #
# ─────────────────────────────────────────────────────────────────────────── #


def test_plan_mask_intent_with_valid_table(tmp_path):
    llm = FakeLLMProvider({"intent": "mask", "table": "p_alt_id_tb", "reasoning": "user asked to mask", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)
    plan = manager.plan("mask the SSNs in p_alt_id_tb")
    assert plan.intent == "mask"
    assert plan.table == "p_alt_id_tb"
    assert "apply_masking_dry_run" in plan.steps
    assert plan.status == PlanStatus.AWAITING_CONFIRMATION


def test_plan_unknown_table_asks_clarifying_question(tmp_path):
    llm = FakeLLMProvider({"intent": "validate", "table": "not_a_real_table", "reasoning": "x", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)
    plan = manager.plan("validate not_a_real_table")
    assert plan.intent == "unclear"
    assert plan.clarifying_question is not None
    assert "not_a_real_table" in plan.clarifying_question


def test_plan_missing_table_becomes_unclear_never_guesses():
    """Section 2.2 rule #4: ambiguity -> clarifying question, never a guessed
    destructive default — even though the LLM said 'mask', with no table
    named the Manager must not pick one on its own."""
    llm = FakeLLMProvider({"intent": "mask", "table": None, "reasoning": "user wants masking", "clarifying_question": None})
    manager = _make_manager(llm, Path("/tmp/phase3-test-missing-table"))
    plan = manager.plan("mask everything")
    assert plan.intent == "unclear"
    assert plan.clarifying_question


def test_plan_llm_unreachable_produces_clarifying_plan(tmp_path):
    llm = FakeLLMProvider(error="connection refused")
    manager = _make_manager(llm, tmp_path)
    plan = manager.plan("validate p_alt_id_tb")
    assert plan.intent == "unclear"
    assert "connection refused" in plan.clarifying_question


def test_plan_unparseable_response_becomes_unclear(tmp_path):
    class GarbageLLM(LLMProvider):
        provider_name = "garbage"
        model = "garbage"

        def complete(self, system_prompt, user_prompt, temperature=0.0):
            return LLMResponse(text="I am not JSON at all, sorry!")

    manager = _make_manager(GarbageLLM(), tmp_path)
    plan = manager.plan("do the thing")
    assert plan.intent == "unclear"


# ─────────────────────────────────────────────────────────────────────────── #
# Full workflow: Plan -> Explain -> Preview -> Confirm -> Execute -> Report    #
# ─────────────────────────────────────────────────────────────────────────── #


def test_full_validate_workflow(tmp_path):
    llm = FakeLLMProvider({"intent": "validate", "table": "p_alt_id_tb", "reasoning": "quality check", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)

    plan = manager.plan("validate p_alt_id_tb")
    assert "I need more information" not in manager.explain(plan)

    preview = manager.preview(plan, SAMPLE_CSV)
    assert preview["validation"]["is_valid"] is True

    confirmed = manager.request_human_confirmation(plan, ask=lambda _msg: True)
    assert confirmed is True

    result = manager.execute(plan, SAMPLE_CSV)
    assert result["action"] == "validate"
    assert result["summary"]["is_valid"] is True

    final_plan = manager.report(plan.plan_id)
    assert "COMPLETED".lower() in final_plan.lower() or "completed" in final_plan


def test_full_mask_workflow_writes_output_and_masks_sensitive_column(tmp_path):
    llm = FakeLLMProvider({"intent": "mask", "table": "p_alt_id_tb", "reasoning": "de-identify SSNs", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)

    plan = manager.plan("mask p_alt_id_tb")
    preview = manager.preview(plan, SAMPLE_CSV)
    assert "p_alt_id" in preview["sensitive_columns"]
    assert preview["masking_coverage"] == 1.0

    assert manager.request_human_confirmation(plan, ask=lambda _msg: True)

    output_path = tmp_path / "masked_output.csv"
    result = manager.execute(plan, SAMPLE_CSV, output_csv=output_path, ruleset=preview["_policy"])
    assert result["action"] == "mask"
    assert output_path.exists()

    import csv as csv_mod

    with open(SAMPLE_CSV, newline="") as fh:
        original_ssns = [row["p_alt_id"] for row in csv_mod.DictReader(fh)]
    with open(output_path, newline="") as fh:
        masked_ssns = [row["p_alt_id"] for row in csv_mod.DictReader(fh)]
    assert masked_ssns != original_ssns
    assert all(len(m) == 64 for m in masked_ssns)  # DETERMINISTIC strategy -> sha256 hex digest


def test_execute_without_confirmation_is_refused(tmp_path):
    """Section 2.2 rule #2: only a CONFIRMED plan may be executed."""
    llm = FakeLLMProvider({"intent": "validate", "table": "p_alt_id_tb", "reasoning": "x", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)
    plan = manager.plan("validate p_alt_id_tb")
    manager.preview(plan, SAMPLE_CSV)
    with pytest.raises(PermissionError):
        manager.execute(plan, SAMPLE_CSV)


def test_rejected_plan_cannot_be_executed(tmp_path):
    llm = FakeLLMProvider({"intent": "validate", "table": "p_alt_id_tb", "reasoning": "x", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)
    plan = manager.plan("validate p_alt_id_tb")
    manager.preview(plan, SAMPLE_CSV)
    confirmed = manager.request_human_confirmation(plan, ask=lambda _msg: False)
    assert confirmed is False
    with pytest.raises(PermissionError):
        manager.execute(plan, SAMPLE_CSV)


def test_plan_persists_to_disk_across_manager_instances(tmp_path):
    """Section 2.1: plans survive context truncation — simulated here by
    reading the plan back via a brand-new PlanMemory pointed at the same dir."""
    llm = FakeLLMProvider({"intent": "validate", "table": "p_alt_id_tb", "reasoning": "x", "clarifying_question": None})
    manager = _make_manager(llm, tmp_path)
    plan = manager.plan("validate p_alt_id_tb")

    reloaded = PlanMemory(tmp_path / "plans").read(plan.plan_id)
    assert reloaded is not None
    assert reloaded.table == "p_alt_id_tb"
    assert reloaded.status == PlanStatus.AWAITING_CONFIRMATION


# ─────────────────────────────────────────────────────────────────────────── #
# Real OllamaProvider: graceful failure when nothing is listening             #
# ─────────────────────────────────────────────────────────────────────────── #


def test_ollama_provider_fails_gracefully_when_unreachable():
    provider = OllamaProvider(base_url="http://localhost:1/v1", model="qwen2:7b", timeout=2)
    response = provider.complete("system", "user")
    assert response.success is False
    assert "qwen2:7b" in response.error


if __name__ == "__main__":
    import subprocess

    subprocess.run(["python3", "-m", "pytest", __file__, "-v"], check=True)
