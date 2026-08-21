"""V7.2 execution metadata protocol.

Keeps execution semantics backward compatible by storing execution decisions
under a dedicated metadata namespace.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

ExecutionStatus = Literal[
    "FULL_APPROVED",
    "CONDITIONAL_APPROVED",
    "REJECTED",
]


def build_execution_metadata(
    status: ExecutionStatus,
    authorized: bool,
    entry_price: Optional[float] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "execution": {
            "status": status,
            "authorized": authorized,
            "conditional_entry_price": entry_price,
            "conditional_entry_reason": reason,
        }
    }


def read_execution_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}
    execution = metadata.get("execution")
    return execution if isinstance(execution, dict) else {}
