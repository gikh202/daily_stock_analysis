# -*- coding: utf-8 -*-
"""V7.2 execution metadata adapter for research ledger.

Keeps execution decision fields isolated from legacy ledger records while
allowing future calibration of conditional entry decisions.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


EXECUTION_STATUS_KEY = "execution_status"
CONDITIONAL_ENTRY_PRICE_KEY = "conditional_entry_price"
CONDITIONAL_ENTRY_REASON_KEY = "conditional_entry_reason"


def build_execution_ledger_fields(
    execution: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract V7.2 execution fields for research ledger persistence."""
    if not execution:
        return {
            EXECUTION_STATUS_KEY: None,
            CONDITIONAL_ENTRY_PRICE_KEY: None,
            CONDITIONAL_ENTRY_REASON_KEY: None,
        }

    return {
        EXECUTION_STATUS_KEY: execution.get("status"),
        CONDITIONAL_ENTRY_PRICE_KEY: execution.get("conditional_entry_price"),
        CONDITIONAL_ENTRY_REASON_KEY: execution.get("conditional_entry_reason"),
    }
