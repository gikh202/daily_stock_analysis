# -*- coding: utf-8 -*-
"""Price-position presentation repair policy.

This module isolates deterministic dashboard repair from ``src.analyzer``. It
fills missing price-position presentation fields from already-computed trend
and realtime quote evidence. It does not fetch data, call an LLM, change
forecast probabilities, apply trading guardrails, persist data, send
notifications, or participate in WAIT_BETTER_ENTRY behavior.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from src.report_language import is_chip_placeholder_value

logger = logging.getLogger(__name__)

_PRICE_POS_KEYS = (
    "ma5",
    "ma10",
    "ma20",
    "bias_ma5",
    "bias_status",
    "current_price",
    "support_level",
    "resistance_level",
)


def _is_value_placeholder(value: Any) -> bool:
    """Return whether a dashboard value is empty or a known placeholder."""
    return is_chip_placeholder_value(value)


def _trend_as_dict(trend_result: Any) -> Dict[str, Any]:
    """Convert the legacy trend result shapes without changing semantics."""
    if isinstance(trend_result, dict):
        return trend_result
    if hasattr(trend_result, "__dict__"):
        return trend_result.__dict__
    return {}


def _quote_as_dict(realtime_quote: Any) -> Dict[str, Any]:
    """Convert the legacy realtime quote shapes without changing semantics."""
    if isinstance(realtime_quote, dict):
        return realtime_quote
    if hasattr(realtime_quote, "to_dict"):
        return realtime_quote.to_dict()
    return {}


def fill_price_position_if_needed(
    result: Any,
    trend_result: Any = None,
    realtime_quote: Any = None,
) -> None:
    """Fill missing ``price_position`` fields from already-computed evidence."""
    if not result:
        return

    try:
        if not result.dashboard:
            result.dashboard = {}
        dashboard = result.dashboard
        data_perspective = dashboard.get("data_perspective") or {}
        dashboard["data_perspective"] = data_perspective
        price_position = data_perspective.get("price_position") or {}

        computed: Dict[str, Any] = {}
        if trend_result:
            trend = _trend_as_dict(trend_result)
            computed["ma5"] = trend.get("ma5")
            computed["ma10"] = trend.get("ma10")
            computed["ma20"] = trend.get("ma20")
            computed["bias_ma5"] = trend.get("bias_ma5")
            computed["current_price"] = trend.get("current_price")

            support_levels = trend.get("support_levels") or []
            resistance_levels = trend.get("resistance_levels") or []
            if support_levels:
                computed["support_level"] = support_levels[0]
            if resistance_levels:
                computed["resistance_level"] = resistance_levels[0]

        if realtime_quote:
            quote = _quote_as_dict(realtime_quote)
            if _is_value_placeholder(computed.get("current_price")):
                computed["current_price"] = quote.get("price")

        filled = False
        for key in _PRICE_POS_KEYS:
            if _is_value_placeholder(price_position.get(key)) and not _is_value_placeholder(
                computed.get(key)
            ):
                price_position[key] = computed[key]
                filled = True

        if filled:
            data_perspective["price_position"] = price_position
            logger.info("[price_position] Filled placeholder fields from computed data")
    except Exception as exc:
        logger.warning("[price_position] Fill failed, skipping: %s", exc)
