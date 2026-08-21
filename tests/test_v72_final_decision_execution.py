from __future__ import annotations

from src.v6_daily.final_decision_service import (
    CONDITIONAL_APPROVED,
    REJECTED,
    _execution_status,
    _serialize_packet,
    _upgrade_execution_contract,
)
from src.v6_daily.fusion_contracts import FinalVerdict, build_final_decision_packet


def _v6(*, decision="WAIT", direction="bullish", opportunity=70.0, risk=40.0):
    return {
        "code": "TEST",
        "instrument_type": "STOCK",
        "effective_trade_date": "2026-08-20",
        "decision": decision,
        "direction": direction,
        "opportunity_score": opportunity,
        "risk_score": risk,
        "forecast_score": 65.0,
        "evidence_coverage": 0.8,
        "trade_plan": {},
        "catalysts": ["constructive catalyst"],
        "risks": ["known risk"],
    }


def _v4(*, direction="bullish", operation="观望"):
    return {
        "forecast": {
            "direction": direction,
            "horizon": "5d",
            "expected_return_pct": 2.0,
        },
        "trend_prediction": direction,
        "operation": operation,
        "is_trading_day": True,
        "watch_conditions": ["wait for confirmation"],
    }


def test_constructive_wait_becomes_conditional_approval_without_execution_authority():
    raw = build_final_decision_packet(_v6(), _v4())
    assert raw.assessment.verdict is FinalVerdict.WAIT
    assert raw.assessment.worth_buying is False

    upgraded = _upgrade_execution_contract(raw)
    assert upgraded.assessment.verdict is FinalVerdict.CONDITIONAL_BUY
    assert upgraded.assessment.worth_buying is True
    assert upgraded.assessment.execution_authorized is False
    assert _execution_status(upgraded) == CONDITIONAL_APPROVED

    serialized = _serialize_packet(upgraded)
    assert serialized["assessment"]["execution_status"] == CONDITIONAL_APPROVED
    assert serialized["assessment"]["conditional_entry_price"] is None
    assert (
        serialized["assessment"]["conditional_entry_reason"]
        == "wait_for_complete_executable_plan"
    )
    assert serialized["execution_contract"]["authorized"] is False


def test_risk_heavy_wait_remains_rejected():
    raw = build_final_decision_packet(
        _v6(opportunity=55.0, risk=70.0),
        _v4(),
    )
    upgraded = _upgrade_execution_contract(raw)
    assert upgraded.assessment.worth_buying is False
    assert _execution_status(upgraded) == REJECTED


def test_direction_conflict_wait_remains_rejected():
    raw = build_final_decision_packet(
        _v6(direction="bullish"),
        _v4(direction="bearish"),
    )
    upgraded = _upgrade_execution_contract(raw)
    assert upgraded.assessment.worth_buying is False
    assert _execution_status(upgraded) == REJECTED


def test_v4_reduce_or_sell_blocks_conditional_upgrade():
    for operation in ("减仓", "卖出"):
        raw = build_final_decision_packet(_v6(), _v4(operation=operation))
        upgraded = _upgrade_execution_contract(raw)
        assert upgraded.assessment.worth_buying is False
        assert _execution_status(upgraded) == REJECTED
