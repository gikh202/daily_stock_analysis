# -*- coding: utf-8 -*-
"""Compatibility facade for the legacy analyzer runtime.

The public ``src.analyzer`` module is intentionally thin.  The historical LLM
adapter/runtime remains import-compatible in ``src.legacy.analyzer_impl`` while
deterministic policies are rebound here to their dedicated modules so there is
one production source of truth for those responsibilities.

Do not add new business rules to this facade.  New analyzer behavior belongs in
domain/application policy modules or infrastructure adapters.
"""

from __future__ import annotations

from importlib import import_module
import logging
import sys


_PUBLIC_MODULE_NAME = __name__
_LEGACY_MODULE_NAME = "src.legacy.analyzer_impl"
_impl = import_module(_LEGACY_MODULE_NAME)

# Preserve the historical logger category even though implementation source is
# physically isolated under src.legacy.
_impl.logger = logging.getLogger(_PUBLIC_MODULE_NAME)

# --- Deterministic analyzer policies: production single sources of truth. ---
from src.chip_presentation_policy import (  # noqa: E402
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
from src.price_position_policy import fill_price_position_if_needed  # noqa: E402
from src.structural_decision_policy import stabilize_decision_with_structure  # noqa: E402
from src.trend_prompt_policy import (  # noqa: E402
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

# Retain legacy public introspection/pickling names for objects that were
# historically defined in src.analyzer.  Their globals still point at the
# implementation module, so monkeypatching attributes on src.analyzer continues
# to affect runtime behavior.
for _value in list(vars(_impl).values()):
    if getattr(_value, "__module__", None) == _LEGACY_MODULE_NAME:
        try:
            _value.__module__ = _PUBLIC_MODULE_NAME
        except (AttributeError, TypeError):
            pass

_impl.__architecture_legacy_impl__ = _LEGACY_MODULE_NAME
_impl.__name__ = _PUBLIC_MODULE_NAME
_impl.__package__ = "src"

# Module replacement is deliberate: callers and monkeypatches receive the
# implementation module object, not a proxy, preserving historical module-level
# seams such as src.analyzer.Router/get_config/create_generation_backend.
sys.modules[_PUBLIC_MODULE_NAME] = _impl
