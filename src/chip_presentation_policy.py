# -*- coding: utf-8 -*-
"""Chip-distribution presentation and availability policy.

This module isolates deterministic chip-evidence normalization from
``src.analyzer``. It does not participate in LLM prompting, model routing,
scoring, trading decisions, guardrails, persistence, notification delivery, or
WAIT_BETTER_ENTRY behavior.

The function names intentionally match the legacy analyzer helpers so the
existing callers can migrate behind thin compatibility facades in a later,
mechanically small change.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

from src.report_language import (
    get_chip_unavailable_text,
    is_chip_placeholder_value,
    localize_chip_health,
    normalize_report_language,
)

logger = logging.getLogger(__name__)

_CHIP_KEYS: tuple = ("profit_ratio", "avg_cost", "concentration", "chip_health")


def _is_value_placeholder(value: Any) -> bool:
    """Return whether a dashboard value is empty or a known placeholder."""
    return is_chip_placeholder_value(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert to float while preserving the legacy fallback semantics."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        try:
            return default if math.isnan(float(value)) else float(value)
        except (ValueError, TypeError):
            return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _coerce_chip_metric(value: Any) -> Optional[float]:
    """Convert chip metrics while preserving the distinction between missing and zero."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        try:
            numeric = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    return None if math.isnan(numeric) else numeric


def _derive_chip_health(
    profit_ratio: float,
    concentration_90: float,
    language: str = "zh",
) -> str:
    """Derive the localized chip-health label from profit and concentration."""
    if profit_ratio >= 0.9:
        return localize_chip_health("警惕", language)
    if concentration_90 >= 0.25:
        return localize_chip_health("警惕", language)
    if concentration_90 < 0.15 and 0.3 <= profit_ratio < 0.9:
        return localize_chip_health("健康", language)
    return localize_chip_health("一般", language)


def _build_chip_structure_from_data(
    chip_data: Any,
    language: str = "zh",
) -> Dict[str, Any]:
    """Build the report ``chip_structure`` block from provider data."""
    if hasattr(chip_data, "profit_ratio"):
        profit_ratio = _safe_float(chip_data.profit_ratio)
        avg_cost = chip_data.avg_cost
        concentration_90 = _safe_float(chip_data.concentration_90)
    else:
        data = chip_data if isinstance(chip_data, dict) else {}
        profit_ratio = _safe_float(data.get("profit_ratio"))
        avg_cost = data.get("avg_cost")
        concentration_90 = _safe_float(data.get("concentration_90"))

    chip_health = _derive_chip_health(
        profit_ratio,
        concentration_90,
        language=language,
    )
    return {
        "profit_ratio": f"{profit_ratio:.1%}",
        "avg_cost": (
            avg_cost
            if (avg_cost is not None and _safe_float(avg_cost) != 0.0)
            else "N/A"
        ),
        "concentration": f"{concentration_90:.2%}",
        "chip_health": chip_health,
    }


def _has_meaningful_chip_data(chip_data: Any) -> bool:
    """Return whether chip data has the core metrics required for reporting."""
    if not chip_data:
        return False

    if hasattr(chip_data, "avg_cost"):
        avg_cost = _coerce_chip_metric(getattr(chip_data, "avg_cost", None))
        concentration_90 = _coerce_chip_metric(
            getattr(chip_data, "concentration_90", None)
        )
        concentration_70 = _coerce_chip_metric(
            getattr(chip_data, "concentration_70", None)
        )
    else:
        data = chip_data if isinstance(chip_data, dict) else {}
        avg_cost = _coerce_chip_metric(data.get("avg_cost"))
        concentration_90_value = data.get("concentration_90")
        if concentration_90_value is None:
            concentration_90_value = data.get("concentration")
        concentration_90 = _coerce_chip_metric(concentration_90_value)
        concentration_70 = _coerce_chip_metric(data.get("concentration_70"))

    return (
        avg_cost is not None
        and avg_cost > 0
        and (
            (concentration_90 is not None and concentration_90 >= 0)
            or (concentration_70 is not None and concentration_70 >= 0)
        )
    )


def _mark_chip_structure_unavailable(result: Any, language: str) -> None:
    """Mark provider-unavailable chip evidence without manufacturing a score."""
    if not result or not isinstance(result.dashboard, dict):
        return
    data_perspective = result.dashboard.get("data_perspective")
    if not isinstance(data_perspective, dict):
        return
    data_perspective["chip_structure"] = {}
    data_perspective["chip_unavailable_reason"] = get_chip_unavailable_text(language)


def _mark_chip_structure_disabled(result: Any, language: str) -> None:
    """Mark intentionally disabled chip evidence without treating it as bearish."""
    if not result or not isinstance(result.dashboard, dict):
        return
    data_perspective = result.dashboard.get("data_perspective")
    if not isinstance(data_perspective, dict):
        return

    data_perspective["chip_structure"] = {}
    normalized = normalize_report_language(language)
    if normalized == "zh":
        reason = "筹码分布功能已禁用；本项不参与评分，也不作为负面证据。"
    elif normalized == "ko":
        reason = "칩 분포 기능이 비활성화되어 있으며, 점수나 부정적 근거에 사용하지 않습니다."
    else:
        reason = (
            "Chip distribution is disabled; this item is excluded from scoring "
            "and is not bearish evidence."
        )
    data_perspective["chip_unavailable_reason"] = reason


def normalize_chip_structure_availability(
    result: Any,
    chip_data: Any,
    *,
    feature_enabled: bool = True,
) -> None:
    """Normalize optional chip evidence without treating missing data as bearish."""
    if not result:
        return

    language = getattr(result, "report_language", "zh")
    if _has_meaningful_chip_data(chip_data):
        fill_chip_structure_if_needed(result, chip_data)
        return
    if not feature_enabled:
        _mark_chip_structure_disabled(result, language)
        return
    _mark_chip_structure_unavailable(result, language)


def fill_chip_structure_if_needed(result: Any, chip_data: Any) -> None:
    """Fill placeholder chip fields from provider data, preserving LLM extras."""
    if not result or not _has_meaningful_chip_data(chip_data):
        return

    try:
        if not result.dashboard:
            result.dashboard = {}
        dashboard = result.dashboard
        data_perspective = dashboard.get("data_perspective") or {}
        dashboard["data_perspective"] = data_perspective
        chip_structure = data_perspective.get("chip_structure") or {}
        filled = _build_chip_structure_from_data(
            chip_data,
            language=getattr(result, "report_language", "zh"),
        )

        merged = dict(chip_structure)
        for key in _CHIP_KEYS:
            if _is_value_placeholder(merged.get(key)):
                merged[key] = filled[key]
        if merged != chip_structure:
            data_perspective["chip_structure"] = merged
            logger.info(
                "[chip_structure] Filled placeholder chip fields from data source (Issue #589)"
            )
    except Exception as exc:
        logger.warning("[chip_structure] Fill failed, skipping: %s", exc)
