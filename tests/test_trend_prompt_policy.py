# -*- coding: utf-8 -*-
"""Characterization tests for the extracted trend prompt consistency policy."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.analyzer import (
    _contains_trend_hint as legacy_contains_trend_hint,
    _infer_trend_direction as legacy_infer_trend_direction,
    _normalize_prompt_reason_items as legacy_normalize_prompt_reason_items,
    _sanitize_trend_analysis_for_prompt as legacy_sanitize_trend_analysis_for_prompt,
)
from src.trend_prompt_policy import (
    _BEARISH_TREND_HINTS,
    _BULLISH_TREND_HINTS,
    _contains_trend_hint,
    _infer_trend_direction,
    _normalize_prompt_reason_items,
    _sanitize_trend_analysis_for_prompt,
)


@pytest.mark.parametrize(
    "items",
    [
        None,
        "not-a-list",
        [],
        ["", "  ", None, "多头排列", 123],
        ["风险A", "风险B"],
    ],
)
def test_reason_item_normalization_matches_legacy(items) -> None:
    assert _normalize_prompt_reason_items(items) == legacy_normalize_prompt_reason_items(items)


@pytest.mark.parametrize(
    "text, hints",
    [
        ("当前形成多头排列", _BULLISH_TREND_HINTS),
        ("当前不是多头排列", _BULLISH_TREND_HINTS),
        ("尚未形成多头排列", _BULLISH_TREND_HINTS),
        ("并非趋势向上，而是趋势向下", _BULLISH_TREND_HINTS),
        ("not bullish", _BULLISH_TREND_HINTS),
        ("bullish momentum", _BULLISH_TREND_HINTS),
        ("当前空头排列", _BEARISH_TREND_HINTS),
        ("没有形成空头排列", _BEARISH_TREND_HINTS),
        ("downtrend", _BEARISH_TREND_HINTS),
    ],
)
def test_trend_hint_negation_matches_legacy(text, hints) -> None:
    assert _contains_trend_hint(text, hints) == legacy_contains_trend_hint(text, hints)


@pytest.mark.parametrize(
    "trend",
    [
        {},
        {"trend_status": "多头排列"},
        {"trend_status": "空头排列"},
        {"trend_status": "不是多头排列"},
        {"ma_alignment": "MA5>MA10>MA20"},
        {"ma_alignment": "MA5<MA10<MA20"},
        {"ma_alignment": "MA5>MA10, MA10<=MA20"},
        {"ma_alignment": "MA5<MA10, MA10>=MA20"},
        {"trend_status": "bullish", "ma_alignment": "MA5<MA10<MA20"},
        {"trend_status": "neutral", "ma_alignment": "sideways"},
    ],
)
def test_trend_direction_matches_legacy(trend) -> None:
    assert _infer_trend_direction(trend) == legacy_infer_trend_direction(trend)


@pytest.mark.parametrize(
    "trend, volume_change_ratio",
    [
        ({}, None),
        (
            {
                "trend_status": "空头排列",
                "signal_reasons": ["多头排列确认", "MACD改善", ""],
                "risk_factors": ["跌破均线"],
            },
            None,
        ),
        (
            {
                "trend_status": "多头排列",
                "signal_reasons": ["空头排列风险", "量价配合"],
                "risk_factors": ["趋势向下", "估值偏高"],
            },
            None,
        ),
        (
            {
                "trend_status": "多头排列",
                "signal_reasons": ["不是空头排列", "趋势向上"],
                "risk_factors": [],
            },
            "12.5",
        ),
        (
            {
                "trend_status": "neutral",
                "signal_reasons": None,
                "risk_factors": "not-list",
                "extra": {"keep": True},
            },
            float("nan"),
        ),
        (
            {
                "trend_status": "弱势空头",
                "signal_reasons": ["事件催化", "上升趋势"],
                "risk_factors": ["波动扩大"],
            },
            10.01,
        ),
    ],
)
def test_sanitize_trend_prompt_policy_matches_legacy(trend, volume_change_ratio) -> None:
    original = deepcopy(trend)

    actual = _sanitize_trend_analysis_for_prompt(
        trend,
        volume_change_ratio=volume_change_ratio,
    )
    expected = legacy_sanitize_trend_analysis_for_prompt(
        trend,
        volume_change_ratio=volume_change_ratio,
    )

    assert actual == expected
    assert trend == original


def test_non_dict_trend_matches_legacy() -> None:
    assert _sanitize_trend_analysis_for_prompt(None) == legacy_sanitize_trend_analysis_for_prompt(None)
    assert _sanitize_trend_analysis_for_prompt(["x"]) == legacy_sanitize_trend_analysis_for_prompt(["x"])
