"""V7.2 execution gate routing.

Keeps investment conviction separate from execution permission.
"""

from api.v1.schemas.execution_decision import ExecutionDecision


class ExecutionGate:
    """Translate execution status into trading workflow decisions."""

    @staticmethod
    def route(decision: ExecutionDecision) -> str:
        if decision.execution_status == "FULL_APPROVED":
            return "EXECUTE_WITH_INTRADAY_OPTIMIZATION"

        if decision.execution_status == "CONDITIONAL_APPROVED":
            return "WAIT_FOR_CONFIRMATION"

        return "BLOCK_NEW_POSITION"

    @staticmethod
    def can_open_position(decision: ExecutionDecision) -> bool:
        return decision.execution_status == "FULL_APPROVED"

    @staticmethod
    def needs_price_confirmation(decision: ExecutionDecision) -> bool:
        return decision.execution_status == "CONDITIONAL_APPROVED"
