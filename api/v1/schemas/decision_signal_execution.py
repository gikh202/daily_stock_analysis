"""V7.2 DecisionSignal execution bridge.

Keeps DecisionSignal payloads backward compatible while introducing
execution status semantics.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from api.v1.schemas.execution_decision import ExecutionStatus


class DecisionSignalExecution(BaseModel):
    """Execution metadata attached to a decision signal.

    Investment conviction and execution timing are separated:
    - FULL_APPROVED: executable, intraday layer may optimize.
    - CONDITIONAL_APPROVED: valid thesis, waiting for trigger.
    - REJECTED: no new position.
    """

    execution_status: ExecutionStatus = "REJECTED"
    execution_authorized: bool = False

    conditional_entry_price: Optional[float] = Field(default=None, gt=0)
    conditional_entry_reason: Optional[str] = None

    def sync_authorization(self) -> None:
        self.execution_authorized = self.execution_status == "FULL_APPROVED"
