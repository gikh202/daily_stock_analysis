"""V7.2 execution decision schema foundation.

Separates investment conviction from execution timing.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


ExecutionStatus = Literal[
    "FULL_APPROVED",
    "CONDITIONAL_APPROVED",
    "REJECTED",
]


class ExecutionDecision(BaseModel):
    """Execution gate used by decision and intraday layers.

    FULL_APPROVED:
        The symbol can be executed and intraday logic may optimize entry.
    CONDITIONAL_APPROVED:
        The thesis is valid but requires price/condition confirmation.
    REJECTED:
        New positions are not allowed.
    """

    execution_status: ExecutionStatus = "REJECTED"

    # Backward compatibility with V7 execution gate.
    execution_authorized: bool = False

    conditional_entry_price: Optional[float] = Field(default=None, gt=0)
    conditional_entry_reason: Optional[str] = None

    def is_executable(self) -> bool:
        return self.execution_status == "FULL_APPROVED"

    def requires_confirmation(self) -> bool:
        return self.execution_status == "CONDITIONAL_APPROVED"

    def sync_legacy_authorization(self) -> None:
        self.execution_authorized = self.execution_status == "FULL_APPROVED"
