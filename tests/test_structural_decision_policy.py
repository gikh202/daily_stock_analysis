# -*- coding: utf-8 -*-
"""Characterization tests for the extracted structural decision policy."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.analyzer import (
    _capital_flow_bias_with_status as legacy_capital_flow_bias_with_status,
    _has_structural_risk_alert as legacy_has_structural_risk_alert,
    stabilize_decision_with_structure as legacy_stabilize_decision_with_structure,
)
from src.structural_decision_policy import (
    _capital_flow_bias_with_status,
    _has_structural_risk_alert,
    stabilize_decision_with_structure,
)


def _result(
    *,
    decision_type="hold",
    operation_advice="持有",
    sentiment_score=50,
    current_price=100,
    support=95,
    resistance=105,
    change_pct=0,
    risk_warning="",
    language="zh",
    dashboard_extra=None,
):
    dashboard = {
        "data_perspective": {
            "price_position": {
                "current_price": current_price,
                "support_level": support,
                "resistance_level": resistance,
            }
        }
    }
    if dashboard_extra:
        dashboard.update(deepcopy(dashboard_extra))
    return SimpleNamespace(
        report_language=language,
        dashboard=dashboard,
        current_price=current_price,
        decision_type=decision_type,
        operation_advice=operation_advice,
        sentiment_score=sentiment_score,
        trend_prediction="看多" if decision_type == "buy" else "震荡",
        risk_warning=risk_warning,
        buy_reason="",
        confidence_level="中",
        change_pct=change_pct,
    )


def _flow(main=0, d5=0, d10=0, *, status="ok"):
    return {
        "capital_flow": {
            "status": status,
            "data": {
                "stock_flow": {
                    "main_net_inflow": main,
                    "inflow_5d": d5,
                    "inflow_10d": d10,
                }
            },
        }
    }


def _run_both(result, trend_result, fundamental_context):
    new_result = deepcopy(result)
    legacy_result = deepcopy(result)

    stabilize_decision_with_structure(
        new_result,
        deepcopy(trend_result),
        deepcopy(fundamental_context),
    )
    legacy_stabilize_decision_with_structure(
        legacy_result,
        deepcopy(trend_result),
        deepcopy(fundamental_context),
    )

    assert new_result.__dict__ == legacy_result.__dict__
    return new_result


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"capital_flow": None},
        {"capital_flow": {"status": "not_supported"}},
        {"capital_flow": {"status": "ok", "data": {"stock_flow": {}}}},
        _flow(10, 20, 30),
        _flow(-10, -20, -30),
        _flow(10, -20, 30),
        _flow(0, 0, 0),
        _flow("1,000", "2,000", "3,000"),
        _flow("N/A", None, "NULL"),
    ],
)
def test_capital_flow_bias_matches_legacy(context) -> None:
    assert _capital_flow_bias_with_status(context) == legacy_capital_flow_bias_with_status(context)


@pytest.mark.parametrize(
    "risk_warning, dashboard_extra",
    [
        ("", None),
        ("N/A", None),
        ("存在重大风险", None),
        ("监管处罚风险", None),
        ("", {"intelligence": {"risk_alerts": "退市风险"}}),
        ("", {"intelligence": {"risk_alerts": ["普通波动", "重大问询"]}}),
        ("", {"core_conclusion": {"signal_type": "重大利空"}}),
    ],
)
def test_structural_risk_detection_matches_legacy(risk_warning, dashboard_extra) -> None:
    result = _result(risk_warning=risk_warning, dashboard_extra=dashboard_extra)
    assert _has_structural_risk_alert(result) == legacy_has_structural_risk_alert(result)


def test_unavailable_capital_flow_records_gap_without_changing_action_or_score() -> None:
    result = _result(decision_type="buy", operation_advice="买入", sentiment_score=72)
    actual = _run_both(
        result,
        None,
        {"capital_flow": {"status": "not_supported"}},
    )
    assert actual.decision_type == "buy"
    assert actual.operation_advice == "买入"
    assert actual.sentiment_score == 72
    assert actual.dashboard["decision_stability"]["applied"] is False


def test_buy_near_resistance_downgrades_to_watch() -> None:
    result = _result(
        decision_type="buy",
        operation_advice="买入",
        sentiment_score=76,
        current_price=103,
        support=95,
        resistance=105,
    )
    actual = _run_both(result, None, _flow(10, -10, 0))
    assert actual.decision_type == "hold"
    assert actual.sentiment_score == 59


def test_buy_with_outflow_downgrades_to_watch() -> None:
    result = _result(
        decision_type="buy",
        operation_advice="买入",
        sentiment_score=70,
        current_price=100,
        support=95,
        resistance=110,
    )
    actual = _run_both(result, None, _flow(-10, -20, -30))
    assert actual.decision_type == "hold"


def test_buy_mid_range_with_neutral_flow_downgrades() -> None:
    result = _result(
        decision_type="buy",
        operation_advice="买入",
        sentiment_score=68,
        current_price=100,
        support=90,
        resistance=115,
    )
    actual = _run_both(result, None, _flow(10, -10, 0))
    assert actual.decision_type == "hold"


def test_sell_near_support_without_outflow_downgrades() -> None:
    result = _result(
        decision_type="sell",
        operation_advice="卖出",
        sentiment_score=25,
        current_price=96,
        support=95,
        resistance=110,
    )
    actual = _run_both(result, None, _flow(10, -10, 0))
    assert actual.decision_type == "hold"
    assert actual.sentiment_score == 45


def test_sell_with_inflow_downgrades_when_support_not_broken() -> None:
    result = _result(
        decision_type="sell",
        operation_advice="卖出",
        sentiment_score=30,
        current_price=100,
        support=90,
        resistance=110,
    )
    actual = _run_both(result, None, _flow(10, 20, 30))
    assert actual.decision_type == "hold"


def test_significant_risk_preserves_sell_near_support() -> None:
    result = _result(
        decision_type="sell",
        operation_advice="卖出",
        sentiment_score=25,
        current_price=96,
        support=95,
        resistance=110,
        risk_warning="存在退市风险",
    )
    actual = _run_both(result, None, _flow(10, -10, 0))
    assert actual.decision_type == "sell"


def test_hold_negative_near_support_uses_shakeout_wording() -> None:
    result = _result(
        decision_type="hold",
        operation_advice="持有",
        sentiment_score=50,
        current_price=96,
        support=95,
        resistance=110,
        change_pct=-2.0,
    )
    actual = _run_both(result, None, _flow(10, -10, 0))
    assert actual.decision_type == "hold"
    assert actual.operation_advice == "洗盘观察"


def test_hold_mid_range_neutral_flow_uses_range_wording() -> None:
    result = _result(
        decision_type="hold",
        operation_advice="持有",
        sentiment_score=52,
        current_price=100,
        support=90,
        resistance=115,
    )
    actual = _run_both(result, None, _flow(10, -10, 0))
    assert actual.operation_advice == "震荡观望"


def test_trend_result_can_supply_missing_prices() -> None:
    result = _result(
        decision_type="hold",
        current_price=None,
        support=None,
        resistance=None,
        change_pct=-1,
    )
    result.dashboard = {"data_perspective": {"price_position": {}}}
    trend = {
        "current_price": "96.0",
        "support_levels": ["95"],
        "resistance_levels": ["110"],
    }
    _run_both(result, trend, _flow(10, -10, 0))
