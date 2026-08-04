"""
core/audit.py — Append-only audit log skeleton.

Section 2.2.3: "Every tool call, every agent handoff, every human
confirmation/rejection is written to an append-only audit log
(who/what/when/before-value/after-value where applicable)."

Section 10.6: "Audit logs must redact secret values even when logging the
config keys that were touched."

Phase-1 scope: a local JSON-Lines append-only sink (one record per line,
never rewritten/rotated in place) that a later phase can point at a proper
audit store (Postgres table, or an event bus topic) without changing the
call sites below. `AuditLog.record()` is the ONE function every agent /
adapter / DBA-console action should call.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SECRET_KEY_PATTERN = re.compile(
    r"(pass|pwd|secret|token|api[_-]?key|credential)", re.IGNORECASE
)
_REDACTED = "***REDACTED***"


def _redact(value: Any) -> Any:
    """Recursively redact dict values whose key looks secret-ish."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SECRET_KEY_PATTERN.search(str(k)):
                out[k] = _REDACTED
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


@dataclass
class AuditRecord:
    timestamp: str
    actor: str                       # user id, agent name, or "system"
    action: str                      # e.g. "execute_sql", "agent_handoff", "human_confirmation"
    resource: str                    # e.g. "provider.p_dtl_tb", "manager->validation_agent"
    before: Optional[dict] = None
    after: Optional[dict] = None
    result: str = "unknown"          # "success" | "failure" | "pending"
    detail: Optional[dict] = None

    def to_json(self) -> str:
        redacted = asdict(self)
        redacted["before"] = _redact(redacted.get("before"))
        redacted["after"] = _redact(redacted.get("after"))
        redacted["detail"] = _redact(redacted.get("detail"))
        return json.dumps(redacted, default=str)


class AuditLog:
    """
    Thread-safe, append-only JSON-Lines audit sink.

    Phase-1 note: this is a local-file stand-in. It intentionally exposes
    only `record()` as the write path so swapping the storage backend later
    (Postgres audit table / Kafka topic) touches this one class, not every
    call site across the codebase.
    """

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        actor: str,
        action: str,
        resource: str,
        result: str = "success",
        before: Optional[dict] = None,
        after: Optional[dict] = None,
        detail: Optional[dict] = None,
    ) -> AuditRecord:
        rec = AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=actor,
            action=action,
            resource=resource,
            before=before,
            after=after,
            result=result,
            detail=detail,
        )
        line = rec.to_json()
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return rec

    def read_all(self) -> list[dict]:
        """Convenience reader for the Audit Dashboard / tests. Never used for writes."""
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
