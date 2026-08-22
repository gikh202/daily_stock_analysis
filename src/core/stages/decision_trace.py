# -*- coding: utf-8 -*-
"""Decision trace and forecast/execution projection stage.

The stage contains pure bookkeeping that records pre/post-guardrail decision
state and projects the final execution view. It intentionally has no market
data, LLM, storage, notification, or WAIT_BETTER_ENTRY dependencies.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


class DecisionTraceStage:
    """Pure helpers for decision trace capture and final execution projection."""

    @staticmethod
    def decision_state_snapshot(result: Optional[Any]) -> Dict[str, Any]:
        if result is None:
            return {}
        return {
            "sentiment_score": getattr(result, "sentiment_score", None),
            "trend_prediction": getattr(result, "trend_prediction", None),
            "operation_advice": getattr(result, "operation_advice", None),
            "decision_type": getattr(result, "decision_type", None),
            "action": getattr(result, "action", None),
        }

    @staticmethod
    def technical_prediction_snapshot(trend_result: Optional[Any]) -> Dict[str, Any]:
        if trend_result is None:
            return {"status": "unavailable"}
        return {
            "status": "available",
            "signal_score": getattr(trend_result, "signal_score", None),
            "buy_signal": (
                getattr(getattr(trend_result, "buy_signal", None), "value", None)
                or str(getattr(trend_result, "buy_signal", ""))
            ),
            "trend_status": (
                getattr(getattr(trend_result, "trend_status", None), "value", None)
                or str(getattr(trend_result, "trend_status", ""))
            ),
            "trend_strength": getattr(trend_result, "trend_strength", None),
        }

    @staticmethod
    def append_guardrail_trace(
        trace: Dict[str, Any],
        *,
        name: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        adjustments: Any,
    ) -> None:
        guardrails = trace.setdefault("guardrails", [])
        guardrails.append(
            {
                "name": name,
                "adjustments": list(adjustments or []),
                "before": before,
                "after": after,
            }
        )

    @classmethod
    def finalize_prediction_execution_split(
        cls,
        result: Any,
        *,
        trace: Dict[str, Any],
        market_phase_summary: Optional[Dict[str, Any]],
        forecast_before_guardrails: Any,
    ) -> None:
        final_state = cls.decision_state_snapshot(result)
        trace["final"] = final_state

        forecast_after = copy.deepcopy(getattr(result, "forecast", None))
        trace["forecast_immutable_through_guardrails"] = (
            forecast_after == forecast_before_guardrails
        )
        trace["forecast"] = forecast_after

        phase = None
        if isinstance(market_phase_summary, dict):
            phase = (
                market_phase_summary.get("phase")
                or market_phase_summary.get("market_phase")
            )

        result.execution = {
            "action": getattr(result, "action", None),
            "operation_advice": getattr(result, "operation_advice", None),
            "decision_type": getattr(result, "decision_type", None),
            "execution_score": getattr(result, "sentiment_score", None),
            "market_phase": phase,
            "note": (
                "Execution may be watch/hold even when forecast is bullish; "
                "market phase and entry discipline belong to the execution layer."
            ),
        }
        result.decision_trace = trace

        if isinstance(result.dashboard, dict):
            result.dashboard["forecast"] = copy.deepcopy(
                getattr(result, "forecast", None)
            )
            result.dashboard["execution"] = copy.deepcopy(result.execution)
            result.dashboard["decision_trace"] = copy.deepcopy(trace)
