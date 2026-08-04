"""
agent/subagents/correction_agent.py — Section 2.1's Correction/Suggestion Agent.

Tools it owns: `suggest_correction`.

"Proposes fixes for invalid values (date, phone, email, etc.) as a diff,
never applies them directly." Every method here returns a
before/after/applied=False diff — nothing in this class writes to a row,
matching the spec's own phrasing exactly. Section 2.4's evaluator-optimizer
loop (propose fix -> Validation Agent re-checks -> repeat until clean or
max-iterations) is implemented in `ManagerAgent.run_correction_loop`
(agent/manager.py), which alternates calls to this class and
`ValidationAgent` — this class only ever proposes one fix at a time.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from agent.subagents.base import Subagent, SubagentResult

_PHONE_DIGITS = re.compile(r"\d")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")  # MM/DD/YYYY -> YYYY-MM-DD
_DATE_DOTS = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$")


class CorrectionAgent(Subagent):
    name = "correction_agent"

    def run(self, task: dict[str, Any]) -> SubagentResult:
        action = task.get("action")
        if action == "suggest_correction":
            return self.suggest_correction(task["column"], task["value"], task.get("violation_rule", ""))
        return SubagentResult(agent=self.name, action=str(action), success=False, error=f"Unknown action: {action}")

    def suggest_correction(self, column: str, value: Any, violation_rule: str = "") -> SubagentResult:
        proposed = self._propose(column, value, violation_rule)
        summary = {
            "column": column, "before": value, "after": proposed,
            "applied": False,  # never applies — the Manager/human decides
            "fixable": proposed is not None,
        }
        return SubagentResult(agent=self.name, action="suggest_correction", success=True, summary=summary)

    @staticmethod
    def _propose(column: str, value: Any, violation_rule: str) -> Optional[str]:
        if value is None:
            return None
        s = str(value).strip()
        col_lower = column.lower()

        if "email" in col_lower:
            candidate = s.lower().replace(" ", "")
            return candidate if _EMAIL_RE.match(candidate) else None

        if "phone" in col_lower or "phn" in col_lower:
            digits = "".join(_PHONE_DIGITS.findall(s))
            if len(digits) == 10:
                return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
            if len(digits) == 11 and digits[0] == "1":
                return f"({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"
            return None

        if "dt" in col_lower or "date" in col_lower:
            m = _DATE_SLASH.match(s)
            if m:
                mm, dd, yyyy = m.groups()
                return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
            m = _DATE_DOTS.match(s)
            if m:
                yyyy, mm, dd = m.groups()
                return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
            return None

        # generic whitespace/case cleanup as a last resort for anything else
        cleaned = " ".join(s.split())
        return cleaned if cleaned != s else None
