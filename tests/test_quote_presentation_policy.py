# -*- coding: utf-8 -*-
"""Characterization tests for the extracted quote-presentation policy."""

from __future__ import annotations

import pytest

from src.analyzer import (
    _phase_aware_quote_labels as legacy_phase_aware_quote_labels,
    _should_hide_regular_session_ohlc as legacy_should_hide_regular_session_ohlc,
    _today_has_realtime_overlay as legacy_today_has_realtime_overlay,
    _today_looks_complete_daily_bar as legacy_today_looks_complete_daily_bar,
)
from src.quote_presentation_policy import (
    _phase_aware_quote_labels,
    _should_hide_regular_session_ohlc,
    _today_has_realtime_overlay,
    _today_looks_complete_daily_bar,
)


@pytest.mark.parametrize(
    "today, expected",
    [
        (None, False),
        ({}, False),
        ({"data_source": "daily"}, False),
        ({"data_source": "realtime:yfinance"}, True),
        ({"dataSource": "realtime:provider"}, True),
        ({"is_partial_bar": True}, True),
        ({"isPartialBar": True}, True),
        ({"is_estimated": True}, True),
        ({"isEstimated": True}, True),
        ({"estimated_fields": ["close"]}, True),
        ({"estimatedFields": ["volume"]}, True),
    ],
)
def test_realtime_overlay_policy_matches_legacy(today, expected) -> None:
    assert _today_has_realtime_overlay(today) is expected
    assert _today_has_realtime_overlay(today) == legacy_today_has_realtime_overlay(today)


@pytest.mark.parametrize(
    "context, phase_context, expected",
    [
        ({}, {}, False),
        ({"today": {"close": None}}, {}, False),
        ({"today": {"close": ""}}, {}, False),
        ({"today": {"close": 100, "data_source": "realtime:x"}}, {}, False),
        (
            {"today": {"close": 100, "date": "2026-08-21"}},
            {"effective_daily_bar_date": "2026-08-21"},
            True,
        ),
        (
            {"today": {"close": 100, "trade_date": "2026-08-21"}},
            {"effective_daily_bar_date": "2026-08-21"},
            True,
        ),
        (
            {"date": "2026-08-21", "today": {"close": 100}},
            {"effective_daily_bar_date": "2026-08-21"},
            True,
        ),
        (
            {"today": {"close": 100, "date": "2026-08-20"}},
            {"effective_daily_bar_date": "2026-08-21"},
            False,
        ),
        ({"today": {"close": 100}}, {}, True),
    ],
)
def test_complete_daily_bar_policy_matches_legacy(context, phase_context, expected) -> None:
    assert _today_looks_complete_daily_bar(context, phase_context) is expected
    assert _today_looks_complete_daily_bar(
        context,
        phase_context,
    ) == legacy_today_looks_complete_daily_bar(context, phase_context)


@pytest.mark.parametrize(
    "context, expected",
    [
        ({}, ("今日行情", "收盘价")),
        ({"market_phase_context": None}, ("今日行情", "收盘价")),
        (
            {
                "today": {"close": 100, "date": "2026-08-21"},
                "market_phase_context": {
                    "phase": "premarket",
                    "effective_daily_bar_date": "2026-08-21",
                },
            },
            ("上一完整交易日行情", "上一完整交易日收盘价"),
        ),
        (
            {
                "today": {"close": 101, "data_source": "realtime:yfinance"},
                "market_phase_context": {"phase": "premarket"},
            },
            ("最新行情", "实时估算价"),
        ),
        (
            {
                "today": {"close": 101, "is_estimated": True},
                "market_phase_context": {"phase": "non_trading"},
            },
            ("最新行情", "实时估算价"),
        ),
        (
            {
                "today": {"close": 101, "date": "2026-08-20"},
                "market_phase_context": {
                    "phase": "premarket",
                    "effective_daily_bar_date": "2026-08-21",
                },
            },
            ("最新行情", "最新价"),
        ),
        (
            {"today": {}, "market_phase_context": {"phase": "premarket"}},
            ("今日行情", "收盘价"),
        ),
        (
            {
                "today": {"close": 101},
                "market_phase_context": {"phase": "intraday", "is_partial_bar": True},
            },
            ("最新行情", "盘中估算价"),
        ),
        (
            {
                "today": {"close": 101},
                "market_phase_context": {"phase": "lunch_break", "is_partial_bar": True},
            },
            ("最新行情", "盘中估算价"),
        ),
        (
            {
                "today": {"close": 101},
                "market_phase_context": {"phase": "closing_auction", "is_partial_bar": True},
            },
            ("最新行情", "盘中估算价"),
        ),
        (
            {
                "today": {"close": 101},
                "market_phase_context": {"phase": "intraday", "is_partial_bar": False},
            },
            ("今日行情", "收盘价"),
        ),
    ],
)
def test_quote_labels_policy_matches_legacy(context, expected) -> None:
    assert _phase_aware_quote_labels(context) == expected
    assert _phase_aware_quote_labels(context) == legacy_phase_aware_quote_labels(context)


@pytest.mark.parametrize(
    "context, expected",
    [
        ({}, False),
        (
            {
                "today": {"close": 100, "date": "2026-08-21"},
                "market_phase_context": {
                    "phase": "premarket",
                    "effective_daily_bar_date": "2026-08-21",
                },
            },
            False,
        ),
        (
            {
                "today": {"close": 101, "data_source": "realtime:yfinance"},
                "market_phase_context": {"phase": "premarket"},
            },
            True,
        ),
        (
            {
                "today": {"close": 101, "is_partial_bar": True},
                "market_phase_context": {"phase": "non_trading"},
            },
            True,
        ),
        (
            {
                "today": {"close": 101, "is_partial_bar": True},
                "market_phase_context": {"phase": "intraday"},
            },
            False,
        ),
    ],
)
def test_hide_regular_ohlc_policy_matches_legacy(context, expected) -> None:
    assert _should_hide_regular_session_ohlc(context) is expected
    assert _should_hide_regular_session_ohlc(context) == legacy_should_hide_regular_session_ohlc(
        context
    )
