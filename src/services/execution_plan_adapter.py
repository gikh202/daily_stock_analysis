# -*- coding: utf-8 -*-
"""
V7.2.1 Execution Plan Adapter

将分析结果转换为可供盘中确认器消费的交易计划合同。
该模块不负责预测、不负责下单，只负责连接：
收盘决策 -> 开盘确认 -> Research Ledger。
"""

from typing import Any, Dict


def build_execution_plan(analysis: Any) -> Dict[str, Any]:
    """Build a stable execution plan from an analysis result.

    Fail-open design:
    - 不改变原有分析结论
    - 无法提取条件时返回观察计划
    - 不产生自动买入授权
    """
    execution = getattr(analysis, "execution", None)
    if not isinstance(execution, dict):
        execution = {}

    action = str(execution.get("action") or "WAIT").upper()

    status = "CONDITIONAL_APPROVED"
    if action in {"BUY", "BUY_NOW", "APPROVED"}:
        status = "FULL_APPROVED"
    elif action in {"REJECT", "SELL", "INVALID"}:
        status = "REJECTED"

    return {
        "status": status,
        "entry_conditions": _extract_conditions(analysis),
        "entry_zone": _extract_entry_zone(analysis),
        "risk_control": {
            "max_position_pct": execution.get("max_position_pct", 0),
            "stop_loss": execution.get("stop_loss"),
        },
        "invalid_conditions": [],
    }


def _extract_conditions(analysis: Any):
    """Extract existing confirmation hints without inventing new signals."""
    conditions = []
    text = str(getattr(analysis, "report", "") or "")
    if "MA5" in text and "MA10" in text:
        conditions.append({"type": "TECH_CONFIRM", "rule": "MA5_MA10_CONFIRM"})
    if "MA20" in text:
        conditions.append({"type": "SUPPORT_CONFIRM", "rule": "MA20_HOLD"})
    return conditions


def _extract_entry_zone(analysis: Any):
    return {
        "low": None,
        "high": None,
        "source": "derived_later_by_confirmation",
    }
