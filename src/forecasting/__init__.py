from __future__ import annotations

from importlib import import_module
from typing import Any

# Keep package imports lazy. The latency-sensitive US-open workflow only needs
# IntradayTimingModel; importing the package must not eagerly load the complete
# forecast engine/history/decision graph.
_EXPORTS = {
    "ForecastDecisionPolicy": (".decision", "ForecastDecisionPolicy"),
    "V7ForecastEngine": (".engine", "V7ForecastEngine"),
    "ForecastHistory": (".history", "ForecastHistory"),
    "ForecastBundle": (".models", "ForecastBundle"),
    "ForecastDecision": (".models", "ForecastDecision"),
    "ForecastHorizon": (".models", "ForecastHorizon"),
    "TimingAssessment": (".models", "TimingAssessment"),
    "IntradayTimingModel": (".timing", "IntradayTimingModel"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
