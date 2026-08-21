# -*- coding: utf-8 -*-
"""V7.2.1 execution plan contract.

The execution plan bridges close analysis decisions and next-session
execution confirmation. It intentionally does not place orders; it only
records conditions that the intraday layer can evaluate.
"""

from typing import Any, Dict, List, Optional


def build_execution_plan(
    status: str,
    *,
    entry_conditions: Optional[List[Dict[str, Any]]] = None,
    entry_zone: Optional[Dict[str, Any]] = None,
    max_position_pct: float = 0.0,
    stop_loss: Optional[float] = None,
    invalid_conditions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a normalized execution plan contract."""
    return {
        "status": status,
        "entry_conditions": entry_conditions or [],
        "entry_zone": entry_zone or {},
        "risk_control": {
            "max_position_pct": max_position_pct,
            "stop_loss": stop_loss,
        },
        "invalid_conditions": invalid_conditions or [],
    }
