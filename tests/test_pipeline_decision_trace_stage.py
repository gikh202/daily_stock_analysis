# -*- coding: utf-8 -*-
"""Characterization tests for the extracted pipeline decision-trace stage."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from types import SimpleNamespace

import pytest

from src.core.pipeline import StockAnalysisPipeline
from src.core.stages.decision_trace import DecisionTraceStage


class _Signal(Enum):
    BUY = "buy"


class _Trend(Enum):
    UP = "up"


def _analysis_result(*, dashboard=True):
    return SimpleNamespace(
        sentiment_score=67,
        trend_prediction="看多",
        operation_advice="持有",
        decision_type="hold",
        action="watch",
        forecast={"1d": {"p_up": 0.61}},
        execution=None,
        decision_trace=None,
        dashboard={} if dashboard else None,
    )


def test_decision_state_snapshot_matches_pipeline_legacy() -> None:
    result = _analysis_result()
    assert DecisionTraceStage.decision_state_snapshot(result) == StockAnalysisPipeline._decision_state_snapshot(result)
    assert DecisionTraceStage.decision_state_snapshot(None) == StockAnalysisPipeline._decision_state_snapshot(None)


@pytest.mark.parametrize(
    "trend_result",
    [
        None,
        SimpleNamespace(
            signal_score=80,
            buy_signal=_Signal.BUY,
            trend_status=_Trend.UP,
            trend_strength=0.7,
        ),
        SimpleNamespace(
            signal_score=None,
            buy_signal="custom",
            trend_status="flat",
            trend_strength=None,
        ),
    ],
)
def test_technical_prediction_snapshot_matches_pipeline_legacy(trend_result) -> None:
    assert DecisionTraceStage.technical_prediction_snapshot(trend_result) == StockAnalysisPipeline._technical_prediction_snapshot(trend_result)


def test_append_guardrail_trace_matches_pipeline_legacy() -> None:
    new_trace = {"guardrails": [{"name": "existing"}]}
    legacy_trace = deepcopy(new_trace)

    kwargs = {
        "name": "phase",
        "before": {"decision_type": "buy"},
        "after": {"decision_type": "hold"},
        "adjustments": ("downgrade",),
    }
    DecisionTraceStage.append_guardrail_trace(new_trace, **kwargs)
    StockAnalysisPipeline._append_guardrail_trace(legacy_trace, **kwargs)

    assert new_trace == legacy_trace


def test_finalize_prediction_execution_split_matches_pipeline_legacy() -> None:
    result = _analysis_result()
    legacy_result = deepcopy(result)
    trace = {"initial": {"decision_type": "buy"}, "guardrails": []}
    legacy_trace = deepcopy(trace)
    forecast_before = deepcopy(result.forecast)
    phase = {"phase": "intraday"}

    DecisionTraceStage.finalize_prediction_execution_split(
        result,
        trace=trace,
        market_phase_summary=phase,
        forecast_before_guardrails=forecast_before,
    )
    StockAnalysisPipeline._finalize_prediction_execution_split(
        legacy_result,
        trace=legacy_trace,
        market_phase_summary=phase,
        forecast_before_guardrails=forecast_before,
    )

    assert result.__dict__ == legacy_result.__dict__
    assert trace == legacy_trace
    assert result.execution["market_phase"] == "intraday"
    assert result.decision_trace["forecast_immutable_through_guardrails"] is True


def test_finalize_detects_forecast_mutation_and_supports_market_phase_alias() -> None:
    result = _analysis_result(dashboard=False)
    result.forecast = {"1d": {"p_up": 0.55}}
    trace = {}

    DecisionTraceStage.finalize_prediction_execution_split(
        result,
        trace=trace,
        market_phase_summary={"market_phase": "premarket"},
        forecast_before_guardrails={"1d": {"p_up": 0.60}},
    )

    assert trace["forecast_immutable_through_guardrails"] is False
    assert result.execution["market_phase"] == "premarket"
