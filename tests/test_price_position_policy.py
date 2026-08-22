# -*- coding: utf-8 -*-
"""Characterization tests for the extracted price-position repair policy."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.analyzer import fill_price_position_if_needed as legacy_fill_price_position_if_needed
from src.price_position_policy import fill_price_position_if_needed


def _result(dashboard=None):
    return SimpleNamespace(
        dashboard=deepcopy(dashboard) if dashboard is not None else {},
    )


class _Quote:
    def __init__(self, price):
        self.price = price

    def to_dict(self):
        return {"price": self.price}


@pytest.mark.parametrize(
    "dashboard, trend_result, realtime_quote",
    [
        ({}, None, None),
        (
            {"data_perspective": {"price_position": None}},
            {
                "ma5": 100,
                "ma10": 99,
                "ma20": 98,
                "bias_ma5": 1.5,
                "current_price": 101,
                "support_levels": [97, 95],
                "resistance_levels": [103, 105],
            },
            None,
        ),
        (
            {
                "data_perspective": {
                    "price_position": {
                        "ma5": "N/A",
                        "ma10": 88,
                        "current_price": "数据缺失",
                        "custom": "keep-me",
                    }
                }
            },
            {
                "ma5": 100,
                "ma10": 99,
                "ma20": 98,
                "bias_ma5": 1.5,
                "current_price": None,
                "support_levels": [97],
                "resistance_levels": [103],
            },
            {"price": 101},
        ),
        (
            {"data_perspective": {"price_position": {}}},
            SimpleNamespace(
                ma5=100,
                ma10=99,
                ma20=98,
                bias_ma5=-1.2,
                current_price=97,
                support_levels=[95],
                resistance_levels=[102],
            ),
            _Quote(96),
        ),
        (
            {"data_perspective": {"price_position": {"current_price": 90}}},
            {"current_price": 100},
            {"price": 101},
        ),
        (
            {"data_perspective": {"price_position": {}}},
            {"support_levels": [], "resistance_levels": []},
            _Quote(101),
        ),
    ],
)
def test_price_position_policy_matches_legacy(
    dashboard,
    trend_result,
    realtime_quote,
) -> None:
    new_result = _result(dashboard)
    legacy_result = _result(dashboard)

    fill_price_position_if_needed(new_result, trend_result, realtime_quote)
    legacy_fill_price_position_if_needed(legacy_result, trend_result, realtime_quote)

    assert new_result.dashboard == legacy_result.dashboard


def test_existing_values_win_over_computed_values() -> None:
    dashboard = {
        "data_perspective": {
            "price_position": {
                "ma5": 80,
                "ma10": 81,
                "ma20": 82,
                "current_price": 83,
                "support_level": 75,
                "resistance_level": 90,
                "custom": "keep-me",
            }
        }
    }
    trend = {
        "ma5": 100,
        "ma10": 99,
        "ma20": 98,
        "current_price": 101,
        "support_levels": [97],
        "resistance_levels": [103],
    }
    result = _result(dashboard)

    fill_price_position_if_needed(result, trend, {"price": 102})

    position = result.dashboard["data_perspective"]["price_position"]
    assert position["ma5"] == 80
    assert position["current_price"] == 83
    assert position["support_level"] == 75
    assert position["resistance_level"] == 90
    assert position["custom"] == "keep-me"


def test_realtime_quote_only_fills_current_price_when_trend_price_missing() -> None:
    result = _result({"data_perspective": {"price_position": {}}})

    fill_price_position_if_needed(
        result,
        {"current_price": None},
        _Quote(123.45),
    )

    assert result.dashboard["data_perspective"]["price_position"]["current_price"] == 123.45


def test_falsey_result_is_a_noop() -> None:
    fill_price_position_if_needed(None, {"ma5": 100}, {"price": 101})
