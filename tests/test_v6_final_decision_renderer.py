from __future__ import annotations

import json

import pytest

from src.v6_daily.final_decision_renderer import (
    apply_final_decision_contract,
    assert_final_decision_report_consistency,
)
from src.v6_daily.final_decision_service import (
    FINAL_DECISION_PAYLOAD_VERSION,
    build_final_decision_packets,
    build_final_decision_payload,
)
from src.v6_daily.unified_report import render_integrated_chinese_report


def _payload() -> dict:
    return {
        "version": "v6-cutover-test",
        "generated_at": "2026-08-11T00:00:00Z",
        "market_pulse": {
            "regime": "risk_on",
            "breadth": "neutral",
            "average_opportunity": 77.7,
            "average_risk": 47.1,
            "average_evidence_coverage": 0.77,
        },
        "board": [
            {
                "code": "MSFT",
                "instrument_type": "STOCK",
                "decision": "WATCH",
                "direction": "bullish",
                "forecast_score": 83.5,
                "opportunity_score": 77.7,
                "quality_score": 76.8,
                "risk_score": 47.1,
                "evidence_coverage": 0.77,
                "llm_health": "healthy",
                "features": {},
                "trade_plan": {
                    "action": "WATCH",
                    "entry_zone": [502.0, 506.0],
                    "stop_loss": 487.0,
                    "targets": [544.0, 563.0],
                    "risk_reward": 2.0,
                    "max_position_pct": 0.05,
                },
                "catalysts": ["量化层趋势与相对强弱仍偏多"],
                "risks": ["量化层风险分仍需约束仓位"],
                "limitations": [],
            }
        ],
        "deltas": [],
        "public_context": {},
        "scoreboard": {
            "status": "insufficient_data",
            "minimum_samples": 50,
            "horizons": [],
        },
        "run": {"new_signals": 1, "quick_check": "ok"},
    }


def _record() -> dict:
    raw = {
        "name": "Microsoft Corporation",
        "analysis_summary": "微软中期逻辑偏多，但短期超买，需要等待确认。",
        "risk_warning": "RSI超买且消息面存在风险。",
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": "bullish",
                    "expected_return_pct": 3.0,
                    "rationale": "中期趋势仍向上，但短线需要消化超买。",
                }
            },
        },
        "execution": {"operation_advice": "观望", "action": "watch"},
        "dashboard": {
            "core_conclusion": {"one_sentence": "趋势偏多但不追价。"},
            "intelligence": {
                "positive_catalysts": ["AI与云业务增长继续提供支撑"],
                "risk_alerts": ["短线超买可能引发回调"],
            },
            "battle_plan": {},
            "phase_decision": {
                "watch_conditions": ["回踩后能否获得支撑", "上涨时量能是否放大"],
                "phase_context": {"phase": "premarket", "is_trading_day": True},
            },
            "signal_attribution": {
                "strongest_bullish_signal": "多头排列且相对强弱领先",
                "strongest_bearish_signal": "RSI超买且上涨缩量",
            },
            "data_perspective": {},
        },
    }
    return {
        "id": 100,
        "code": "MSFT",
        "raw_result": json.dumps(raw, ensure_ascii=False),
    }


def test_machine_readable_payload_contains_final_decision_contract() -> None:
    final_payload = build_final_decision_payload(_payload(), v4_records=[_record()])

    assert final_payload["version"] == FINAL_DECISION_PAYLOAD_VERSION
    assert final_payload["summary"]["symbols"] == 1
    assert final_payload["summary"]["fusion_complete"] == 1
    assert final_payload["summary"]["worth_buying"] == 1
    assert final_payload["summary"]["execution_authorized"] == 0
    packet = final_payload["packets"][0]
    assert packet["assessment"]["scope"] == "v4_v6_final_fusion"
    assert packet["assessment"]["is_final"] is True
    assert packet["assessment"]["verdict"] == "conditional_buy"
    assert packet["assessment"]["worth_buying"] is True
    assert packet["assessment"]["execution_authorized"] is False
    assert packet["execution"]["entry_zone"] == [502.0, 506.0]
    assert packet["execution"]["max_position_pct"] == 0.05


def test_typed_renderer_is_already_consistent_and_shim_is_noop() -> None:
    payload = _payload()
    records = [_record()]
    report = render_integrated_chinese_report(
        payload,
        v4_records=records,
        report_date="2026-08-11",
    )
    packets = build_final_decision_packets(payload, v4_records=records)

    assert "**是否值得买**：**条件式可买**" in report
    assert "| 1 | MSFT | 观察 | 方向一致 |" in report
    assert apply_final_decision_contract(report, packets) == report
    assert_final_decision_report_consistency(report, packets)


def test_stale_markdown_fails_closed_instead_of_being_repaired() -> None:
    payload = _payload()
    records = [_record()]
    report = render_integrated_chinese_report(
        payload,
        v4_records=records,
        report_date="2026-08-11",
    )
    stale = report.replace(
        "**是否值得买**：**条件式可买**",
        "**是否值得买**：**暂不买，等待确认**",
        1,
    )
    packets = build_final_decision_packets(payload, v4_records=records)

    with pytest.raises(ValueError, match="final verdict drift"):
        apply_final_decision_contract(stale, packets)
    with pytest.raises(ValueError, match="final verdict drift"):
        assert_final_decision_report_consistency(stale, packets)


def test_missing_v4_gets_explicit_non_final_decision_block() -> None:
    payload = _payload()
    report = render_integrated_chinese_report(
        payload,
        v4_records=[],
        report_date="2026-08-11",
    )
    packets = build_final_decision_packets(payload, v4_records=[])

    assert "**是否值得买**：**数据不足，不能形成最终买入判断**" in report
    assert "V4结构化投研缺失" in report
    assert "| 1 | MSFT | 等待（V4缺失） | V4结构化数据缺失 |" in report
    assert apply_final_decision_contract(report, packets) == report
    assert_final_decision_report_consistency(report, packets)
