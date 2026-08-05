"""
agent/plan_memory.py — Persistent plan memory (Section 2.1: "writes a plan
to persistent memory, not just context — plans must survive context
truncation on long jobs" / "Persist the Manager's plan to durable storage
(DB/Redis), not just in-memory context").

Phase-3 stand-in: one JSON file per plan on local disk. This is the same
"one seam, one class" strategy used for AuditLog in Phase 1 — swapping to
a DB/Redis-backed store in Phase 4 means changing this file only; every
call site (`agent/manager.py`) goes through `create` / `read` / `write` /
`update_status` and never touches the storage format directly.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class PlanStatus(str, Enum):
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Plan:
    plan_id: str
    created_at: str
    updated_at: str
    status: PlanStatus
    nl_request: str
    intent: str
    table: Optional[str] = None
    tables: list[str] = field(default_factory=list)  # Phase 4: multi-table fan-out jobs
    steps: list[str] = field(default_factory=list)
    reasoning: str = ""
    clarifying_question: Optional[str] = None
    preview: Optional[dict] = None
    result: Optional[dict] = None
    subtasks: list[dict] = field(default_factory=list)  # Phase 4: condensed dispatch_subagent() records
    fan_out: bool = False  # Phase 4: True if this plan used multi-agent fan-out (Section 2.3)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class PlanMemory:
    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: str) -> Path:
        return self._dir / f"{plan_id}.json"

    def create(
        self,
        nl_request: str,
        intent: str,
        table: Optional[str] = None,
        tables: Optional[list[str]] = None,
        steps: Optional[list[str]] = None,
        reasoning: str = "",
        clarifying_question: Optional[str] = None,
        fan_out: bool = False,
    ) -> Plan:
        now = datetime.now(timezone.utc).isoformat()
        plan = Plan(
            plan_id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            status=PlanStatus.PLANNING if clarifying_question else PlanStatus.AWAITING_CONFIRMATION,
            nl_request=nl_request,
            intent=intent,
            table=table,
            tables=tables or [],
            steps=steps or [],
            reasoning=reasoning,
            clarifying_question=clarifying_question,
            fan_out=fan_out,
        )
        self.write(plan)
        return plan

    def write(self, plan: Plan) -> None:
        plan.updated_at = datetime.now(timezone.utc).isoformat()
        self._path(plan.plan_id).write_text(json.dumps(plan.to_dict(), indent=2, default=str))

    def read(self, plan_id: str) -> Optional[Plan]:
        path = self._path(plan_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        data["status"] = PlanStatus(data["status"])
        return Plan(**data)

    def update_status(self, plan_id: str, status: PlanStatus, **extra) -> Plan:
        plan = self.read(plan_id)
        if not plan:
            raise KeyError(f"No such plan: {plan_id}")
        plan.status = status
        for k, v in extra.items():
            setattr(plan, k, v)
        self.write(plan)
        return plan

    def list_plans(self) -> list[Plan]:
        plans = []
        for path in sorted(self._dir.glob("*.json")):
            data = json.loads(path.read_text())
            data["status"] = PlanStatus(data["status"])
            plans.append(Plan(**data))
        return plans


class RedisPlanMemory:
    """Phase 7: Redis-backed plan memory for multi-worker production scale-out."""

    def __init__(self, redis_url: str = "redis://localhost:6379/1") -> None:
        import redis
        self.client = redis.Redis.from_url(redis_url)

    def save_plan(self, plan: Any) -> None:
        key = f"plan:{plan.plan_id}"
        self.client.set(key, json.dumps(plan.to_dict(), indent=2))

    def load_plan(self, plan_id: str) -> dict | None:
        key = f"plan:{plan_id}"
        data = self.client.get(key)
        if not data:
            return None
        return json.loads(data.decode("utf-8"))

    def list_plans(self) -> list[dict]:
        keys = self.client.keys("plan:*")
        plans = []
        for k in keys:
            val = self.client.get(k)
            if val:
                plans.append(json.loads(val.decode("utf-8")))
        return plans
