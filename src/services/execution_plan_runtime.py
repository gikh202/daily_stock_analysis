# -*- coding: utf-8 -*-
"""
V7.2.1 Execution Plan Runtime

将 execution plan 注入现有分析结果对象。
不改变预测、不改变原有决策，只增加收盘->开盘确认的结构化契约。
"""

from typing import Any, Dict

from src.services.execution_plan_adapter import build_execution_plan


def enrich_execution_plan(result: Any) -> Any:
    """Attach execution plan to compatible result containers."""
    plan: Dict[str, Any] = build_execution_plan(result)

    execution = getattr(result, "execution", None)
    if isinstance(execution, dict):
        execution["execution_plan"] = plan

    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        metadata["execution_plan"] = plan

    # Support dictionary based report payloads.
    if isinstance(result, dict):
        result.setdefault("execution", {})
        if isinstance(result["execution"], dict):
            result["execution"]["execution_plan"] = plan
        result.setdefault("metadata", {})
        if isinstance(result["metadata"], dict):
            result["metadata"]["execution_plan"] = plan

    return result
