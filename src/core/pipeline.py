# -*- coding: utf-8 -*-
"""Compatibility facade for the application analysis pipeline runtime.

``src.core.pipeline`` remains a stable import/monkeypatch surface while the
orchestration implementation lives in the application layer.  New orchestration
logic belongs under ``src.application.analysis`` rather than this facade.
"""

from __future__ import annotations

from importlib import import_module
import logging
import sys


_PUBLIC_MODULE_NAME = __name__
_IMPL_MODULE_NAME = "src.application.analysis.pipeline_impl"
_public_module = sys.modules[_PUBLIC_MODULE_NAME]
_impl = import_module(_IMPL_MODULE_NAME)

# pipeline_dependencies installs historical constructor patch seams while the
# public facade is still the object registered as src.core.pipeline. Transfer
# those seams to the implementation module before replacing the module entry so
# resolve_pipeline_factory() continues to honor existing monkeypatch targets.
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

# Decision trace bookkeeping now has one production implementation.
from src.core.stages.decision_trace import DecisionTraceStage  # noqa: E402


def _decision_state_snapshot(result):
    return DecisionTraceStage.decision_state_snapshot(result)


def _technical_prediction_snapshot(trend_result):
    return DecisionTraceStage.technical_prediction_snapshot(trend_result)


def _append_guardrail_trace(
    cls,
    trace,
    *,
    name,
    before,
    after,
    adjustments,
):
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

# Preserve historical introspection names for objects defined by the moved
# implementation. Runtime globals remain the implementation module itself.
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
