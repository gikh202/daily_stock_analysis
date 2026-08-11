from __future__ import annotations

import json

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
        "scoreboard": {"status": "insufficient_data", "minimum_samples": 50, "horizons": []},
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
    return {"id": 100, "code": "MSFT", "raw_result": json.dumps(raw, ensure_ascii=False)}


def test_machine_readable_payload_contains_final_decision_contract() -> None:
    final_payload = build_final_decision_payload(_payload(), v4_records=[_record()])

    assert final_payload["version"] == FINAL_DECISION_PAYLOAD_VERSION
    assert final_payload["summary"]["symbols"] == 1
    assert final_payload["summary"]["fusion_complete"] == 1
    assert final_payload["summary"]["worth_buying"] == 1
    packet = final_payload["packets"][0]
    assert packet["assessment"]["scope"] == "v4_v6_final_fusion"
    assert packet["assessment"]["is_final"] is True
    assert packet["assessment"]["verdict"] == "conditional_buy"
    assert packet["assessment"]["worth_buying"] is True
    assert packet["execution"]["entry_zone"] == [502.0, 506.0]
    assert packet["execution"]["max_position_pct"] == 0.05


def test_contract_overwrites_stale_legacy_decision_before_output() -> None:
    payload = _payload()
    records = [_record()]
    legacy = render_integrated_chinese_report(payload, v4_records=records, report_date="2026-08-11")
    deliberately_stale = legacy.replace(
        "**是否值得买**：**条件式可买**",
        "**是否值得买**：**暂不买，等待确认**",
        1,
    )
    assert "**是否值得买**：**暂不买，等待确认**" in deliberately_stale

    packets = build_final_decision_packets(payload, v4_records=records)
    final_report = apply_final_decision_contract(deliberately_stale, packets)

    assert "**是否值得买**：**条件式可买**" in final_report
    assert "**是否值得买**：**暂不买，等待确认**" not in final_report
    assert "| 1 | MSFT | 观察 | 方向一致 |" in final_report
    assert_final_decision_report_consistency(final_report, packets)


def test_missing_v4_gets_explicit_non_final_decision_block() -> None:
    payload = _payload()
    legacy = render_integrated_chinese_report(payload, v4_records=[], report_date="2026-08-11")
    packets = build_final_decision_packets(payload, v4_records=[])

    final_report = apply_final_decision_contract(legacy, packets)

    assert "**是否值得买**：**数据不足，不能形成最终买入判断**" in final_report
    assert "V4结构化投研缺失" in final_report
    assert "| 1 | MSFT | 等待（V4缺失） | V4结构化数据缺失 |" in final_report
    assert_final_decision_report_consistency(final_report, packets)
