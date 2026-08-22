# -*- coding: utf-8 -*-
"""Stable compatibility facade for analysis-pipeline orchestration.

The concrete use-case implementation lives in ``src.application.analysis``.
Historical constructor monkeypatch seams remain supported by the explicit
bootstrap compatibility adapter; new orchestration belongs in the application
layer rather than this module.
"""

from __future__ import annotations

from importlib import import_module
import logging
import sys

_PUBLIC_MODULE_NAME = __name__
_IMPL_MODULE_NAME = "src.application.analysis.pipeline_impl"
_public_module = sys.modules[_PUBLIC_MODULE_NAME]
_impl = import_module(_IMPL_MODULE_NAME)

_LEGACY_FACTORY_SEAMS = (
    "get_db",
    "DataFetcherManager",
    "MarketRegimeAdapter",
    "StockTrendAnalyzer",
    "GeminiAnalyzer",
    "NotificationService",
    "SearchService",
    "MarketStructureService",
    "MarketHotspotService",
    "SocialSentimentService",
)
for _name in _LEGACY_FACTORY_SEAMS:
    if hasattr(_public_module, _name):
        setattr(_impl, _name, getattr(_public_module, _name))

_impl.logger = logging.getLogger(_PUBLIC_MODULE_NAME)

from src.application.analysis.stages.decision_trace import DecisionTraceStage  # noqa: E402


def _decision_state_snapshot(result):
    return DecisionTraceStage.decision_state_snapshot(result)


def _technical_prediction_snapshot(trend_result):
    return DecisionTraceStage.technical_prediction_snapshot(trend_result)


def _append_guardrail_trace(cls, trace, *, name, before, after, adjustments):
    del cls
    return DecisionTraceStage.append_guardrail_trace(
        trace,
        name=name,
        before=before,
        after=after,
        adjustments=adjustments,
    )


def _finalize_prediction_execution_split(
    result,
    *,
    trace,
    market_phase_summary,
    forecast_before_guardrails,
):
    return DecisionTraceStage.finalize_prediction_execution_split(
        result,
        trace=trace,
        market_phase_summary=market_phase_summary,
        forecast_before_guardrails=forecast_before_guardrails,
    )


_impl.StockAnalysisPipeline._decision_state_snapshot = staticmethod(
    _decision_state_snapshot
)
_impl.StockAnalysisPipeline._technical_prediction_snapshot = staticmethod(
    _technical_prediction_snapshot
)
_impl.StockAnalysisPipeline._append_guardrail_trace = classmethod(
    _append_guardrail_trace
)
_impl.StockAnalysisPipeline._finalize_prediction_execution_split = staticmethod(
    _finalize_prediction_execution_split
)

for _value in list(vars(_impl).values()):
    if getattr(_value, "__module__", None) == _IMPL_MODULE_NAME:
        try:
            _value.__module__ = _PUBLIC_MODULE_NAME
        except (AttributeError, TypeError):
            pass

_impl.__architecture_application_impl__ = _IMPL_MODULE_NAME
_impl.__name__ = _PUBLIC_MODULE_NAME
_impl.__package__ = "src.core"
sys.modules[_PUBLIC_MODULE_NAME] = _impl
