from __future__ import annotations

from src.v6_daily.final_decision_service import (
    CONDITIONAL_APPROVED,
    REJECTED,
    _apply_reliability_guard,
    _execution_status,
    _reliability_filtered_v4,
    _upgrade_execution_contract,
)
from src.v6_daily.fusion_contracts import FusionAgreement, build_final_decision_packet


def _v6(
    *,
    samples: int,
    status: str,
    weight: float,
    direction: str = "bullish",
    risk: float = 40.0,
    opportunity: float = 70.0,
):
    return {
        "code": "TEST",
        "instrument_type": "STOCK",
        "effective_trade_date": "2026-08-26",
        "decision": "WAIT",
        "direction": direction,
        "opportunity_score": opportunity,
        "risk_score": risk,
        "forecast_score": 60.0,
        "evidence_coverage": 0.8,
        "trade_plan": {},
        "horizon_forecasts": {
            "10d": {
                "probability_up": 0.60,
                "calibration_samples": samples,
                "calibration_status": status,
                "diagnostics": {"decision_weight": weight},
            }
        },
    }


def _v4(direction: str = "bullish", operation: str = "观望"):
    return {
        "forecast": {
            "direction": direction,
            "horizon": "10d",
            "expected_return_pct": 3.5,
        },
        "trend_prediction": direction,
        "operation": operation,
        "is_trading_day": True,
    }


def test_unreliable_10d_wait_remains_conditionally_observable() -> None:
    v6 = _v6(samples=16, status="shrunk", weight=0.0, direction="bullish")
    filtered = _reliability_filtered_v4(v6, _v4("bullish"))
    assert filtered is not None
    assert filtered["forecast"]["direction"] == "neutral"
    assert filtered["forecast"]["expected_return_pct"] is None
    assert "研究观察" in filtered["forecast"]["horizon"]

    raw = build_final_decision_packet(v6, filtered)
    guarded = _apply_reliability_guard(raw)
    upgraded = _upgrade_execution_contract(guarded)

    assert raw.agreement is FusionAgreement.PARTIAL
    assert upgraded.assessment.execution_authorized is False
    assert upgraded.assessment.worth_buying is True
    assert _execution_status(upgraded) == CONDITIONAL_APPROVED
    assert "uncertainty" in upgraded.assessment.rationale


def test_unreliable_10d_hard_risk_stays_rejected() -> None:
    v6 = _v6(
        samples=16,
        status="shrunk",
        weight=0.0,
        direction="bullish",
        risk=72.0,
        opportunity=60.0,
    )
    filtered = _reliability_filtered_v4(v6, _v4("bullish"))
    raw = build_final_decision_packet(v6, filtered)
    guarded = _apply_reliability_guard(raw)

    assert guarded.assessment.execution_authorized is False
    assert guarded.assessment.worth_buying is False
    assert _execution_status(guarded) == REJECTED


def test_unreliable_10d_sell_operation_stays_rejected() -> None:
    v6 = _v6(samples=16, status="shrunk", weight=0.0, direction="bullish")
    filtered = _reliability_filtered_v4(v6, _v4("bullish", operation="减仓"))
    raw = build_final_decision_packet(v6, filtered)
    guarded = _apply_reliability_guard(raw)
    assert guarded.assessment.worth_buying is False
    assert _execution_status(guarded) == REJECTED


def test_unreliable_10d_cannot_create_direction_conflict() -> None:
    v6 = _v6(samples=16, status="shrunk", weight=0.0, direction="bearish")
    filtered = _reliability_filtered_v4(v6, _v4("bullish"))
    raw = build_final_decision_packet(v6, filtered)
    assert raw.v4_direction == "neutral"
    assert raw.agreement is FusionAgreement.PARTIAL


def test_mature_10d_keeps_existing_cross_layer_behavior() -> None:
    v6 = _v6(samples=60, status="mature", weight=0.21, direction="bullish")
    original = _v4("bullish")
    filtered = _reliability_filtered_v4(v6, original)
    assert filtered == original

    raw = build_final_decision_packet(v6, filtered)
    upgraded = _upgrade_execution_contract(_apply_reliability_guard(raw))
    assert raw.agreement is FusionAgreement.ALIGNED
    assert upgraded.assessment.worth_buying is True
    assert upgraded.assessment.execution_authorized is False
    assert _execution_status(upgraded) == CONDITIONAL_APPROVED
