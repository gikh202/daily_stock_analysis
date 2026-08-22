# -*- coding: utf-8 -*-
"""Architecture contracts for the pipeline application facade."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import src.core.pipeline as pipeline
import src.application.analysis.pipeline_impl as pipeline_impl
from src.core.pipeline_factory_registry import resolve_pipeline_factory
from src.application.analysis.stages.decision_trace import DecisionTraceStage


def test_pipeline_public_module_aliases_application_runtime() -> None:
    assert pipeline is pipeline_impl
    assert (
        pipeline.__architecture_application_impl__
        == "src.application.analysis.pipeline_impl"
    )
    assert pipeline.__name__ == "src.core.pipeline"
    assert pipeline.StockAnalysisPipeline.__module__ == "src.core.pipeline"


def test_pipeline_factory_monkeypatch_seam_survives_module_move() -> None:
    sentinel = object()
    with patch.object(pipeline, "GeminiAnalyzer", sentinel):
        assert resolve_pipeline_factory("GeminiAnalyzer") is sentinel


def test_decision_trace_helpers_delegate_to_stage() -> None:
    result = SimpleNamespace(
        sentiment_score=61,
        trend_prediction="bullish",
        operation_advice="hold",
        decision_type="hold",
        action="watch",
    )
    assert pipeline.StockAnalysisPipeline._decision_state_snapshot(
        result
    ) == DecisionTraceStage.decision_state_snapshot(result)

    trend = SimpleNamespace(
        signal_score=70,
        buy_signal="buy",
        trend_status="up",
        trend_strength=0.8,
    )
    assert pipeline.StockAnalysisPipeline._technical_prediction_snapshot(
        trend
    ) == DecisionTraceStage.technical_prediction_snapshot(trend)


def test_guardrail_trace_helper_delegates_to_stage() -> None:
    trace = {}
    pipeline.StockAnalysisPipeline._append_guardrail_trace(
        trace,
        name="phase",
        before={"action": "buy"},
        after={"action": "watch"},
        adjustments=["downgrade"],
    )
    assert trace["guardrails"] == [
        {
            "name": "phase",
            "adjustments": ["downgrade"],
            "before": {"action": "buy"},
            "after": {"action": "watch"},
        }
    ]
