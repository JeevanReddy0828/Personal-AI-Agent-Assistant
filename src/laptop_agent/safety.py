from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from laptop_agent.audit import AuditLogger


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    risk: RiskLevel
    reason: str
    preview: str | None = None


class ApprovalDenied(RuntimeError):
    pass


class ApprovalGate:
    def __init__(self, ask: Callable[[ApprovalRequest], bool] | None = None, audit: AuditLogger | None = None) -> None:
        self._ask = ask or self._cli_ask
        self._audit = audit

    def require(self, request: ApprovalRequest) -> None:
        if request.risk == RiskLevel.LOW:
            self._record(request, approved=True, skipped=True)
            return
        approved = self._ask(request)
        self._record(request, approved=approved, skipped=False)
        if not approved:
            raise ApprovalDenied(f"Approval denied for: {request.action}")

    def _record(self, request: ApprovalRequest, approved: bool, skipped: bool) -> None:
        if self._audit is None:
            return
        self._audit.record(
            "approval",
            action=request.action,
            risk=request.risk.value,
            reason=request.reason,
            preview=request.preview,
            approved=approved,
            skipped=skipped,
        )

    @staticmethod
    def _cli_ask(request: ApprovalRequest) -> bool:
        print()
        print(f"Approval required: {request.action}")
        print(f"Risk: {request.risk.value}")
        print(f"Reason: {request.reason}")
        if request.preview:
            print("Preview:")
            print(request.preview)
        answer = input("Approve? Type 'yes' to continue: ").strip().lower()
        return answer == "yes"
