"""
agent/subagents/base.py — Base contract every Phase-4 subagent implements.

Section 2.1: "each get a scoped task, a fresh context window, and their
own tool subset. Workers never talk to each other directly — every
coordination decision lives in the orchestrator."

In this codebase "a fresh context window" means: a subagent method
receives exactly the inputs it needs (a table name, a CSV path, a
policy — never the whole Plan object, never another subagent's raw
output) and returns a `SubagentResult`: a small `summary` dict safe to
fold into the Manager's plan/context, plus a `detail_pointer` into
`SharedStorage` for anything large (a 10k-row validation report, a full
masking preview). The Manager reads detail back out only when a human
explicitly asks to see it — never automatically, so normal orchestration
stays cheap.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.shared_storage import DetailPointer, SharedStorage


@dataclass
class SubagentResult:
    agent: str
    action: str
    success: bool
    summary: dict = field(default_factory=dict)
    detail_pointer: Optional[DetailPointer] = None
    error: Optional[str] = None
    runtime_payload: Any = None  # live Python objects (e.g. a MaskingPolicy) passed
    # in-process between the Manager and a subagent — deliberately excluded
    # from to_dict() so nothing non-JSON-serializable ever reaches the
    # audit log or persistent plan memory.

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "action": self.action,
            "success": self.success,
            "summary": self.summary,
            "detail_pointer": self.detail_pointer.pointer if self.detail_pointer else None,
            "detail_record_count": self.detail_pointer.record_count if self.detail_pointer else 0,
            "error": self.error,
        }


class Subagent(ABC):
    """Every subagent owns a fixed, named tool subset (Section 2.1's table)
    exposed as plain methods — `run()` is the single dispatch entry point
    the Manager calls via `dispatch_subagent`, routing to the right tool
    method by `task["action"]`."""

    name: str = "base_subagent"

    def __init__(self, storage: SharedStorage) -> None:
        self._storage = storage

    @abstractmethod
    def run(self, task: dict[str, Any]) -> SubagentResult: ...

    def _store_detail(self, action: str, payload: Any, record_count: int = 0) -> SubagentResult:
        pointer = self._storage.write_detail(payload, record_count=record_count)
        return SubagentResult(agent=self.name, action=action, success=True, detail_pointer=pointer)
