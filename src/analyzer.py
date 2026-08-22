# -*- coding: utf-8 -*-
"""Stable compatibility facade for the analyzer infrastructure runtime.

The concrete LLM adapter remains behavior-compatible in
``src.infrastructure.llm.analyzer_impl``. Deterministic rules are rebound to
canonical domain/presentation/infrastructure policy modules so each behavior
has one production implementation.
"""

from __future__ import annotations

from importlib import import_module
import logging
import sys

_PUBLIC_MODULE_NAME = __name__
_IMPL_MODULE_NAME = "src.infrastructure.llm.analyzer_impl"
_impl = import_module(_IMPL_MODULE_NAME)
_impl.logger = logging.getLogger(_PUBLIC_MODULE_NAME)

from src.presentation.policies.chip import (  # noqa: E402
    _build_chip_structure_from_data,
    _coerce_chip_metric,
    _derive_chip_health,
    _has_meaningful_chip_data,
    _is_value_placeholder,
    _mark_chip_structure_disabled,
    _mark_chip_structure_unavailable,
    fill_chip_structure_if_needed,
    normalize_chip_structure_availability,
)
from src.presentation.policies.price_position import (  # noqa: E402
    fill_price_position_if_needed,
)
from src.domain.decision.structural import (  # noqa: E402
    stabilize_decision_with_structure,
)
from src.infrastructure.llm.trend_prompt import (  # noqa: E402
    _contains_trend_hint,
    _filter_conflicting_trend_items,
    _infer_trend_direction,
    _normalize_prompt_reason_items,
    _sanitize_trend_analysis_for_prompt,
)

_POLICY_EXPORTS = {
    "_build_chip_structure_from_data": _build_chip_structure_from_data,
    "_coerce_chip_metric": _coerce_chip_metric,
    "_derive_chip_health": _derive_chip_health,
    "_has_meaningful_chip_data": _has_meaningful_chip_data,
    "_is_value_placeholder": _is_value_placeholder,
    "_mark_chip_structure_disabled": _mark_chip_structure_disabled,
    "_mark_chip_structure_unavailable": _mark_chip_structure_unavailable,
    "fill_chip_structure_if_needed": fill_chip_structure_if_needed,
    "normalize_chip_structure_availability": normalize_chip_structure_availability,
    "fill_price_position_if_needed": fill_price_position_if_needed,
    "stabilize_decision_with_structure": stabilize_decision_with_structure,
    "_contains_trend_hint": _contains_trend_hint,
    "_filter_conflicting_trend_items": _filter_conflicting_trend_items,
    "_infer_trend_direction": _infer_trend_direction,
    "_normalize_prompt_reason_items": _normalize_prompt_reason_items,
    "_sanitize_trend_analysis_for_prompt": _sanitize_trend_analysis_for_prompt,
}
for _name, _value in _POLICY_EXPORTS.items():
    setattr(_impl, _name, _value)

for _value in list(vars(_impl).values()):
    if getattr(_value, "__module__", None) == _IMPL_MODULE_NAME:
        try:
            _value.__module__ = _PUBLIC_MODULE_NAME
        except (AttributeError, TypeError):
            pass

_impl.__architecture_infrastructure_impl__ = _IMPL_MODULE_NAME
_impl.__name__ = _PUBLIC_MODULE_NAME
_impl.__package__ = "src"
sys.modules[_PUBLIC_MODULE_NAME] = _impl
