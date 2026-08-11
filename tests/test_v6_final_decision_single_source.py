from __future__ import annotations

import json

import src.v6_daily.unified_report as unified_report
from src.v6_daily.final_decision_service import build_final_decision_packets
from src.v6_daily.fusion_contracts import (
    action_label_zh,
    agreement_label_zh,
    render_final_decision_lines,
)


def _payload() -> dict:
    return {
        "version": "v6-single-source-test",
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
        "analysis_summary": "微软中期逻辑偏多，但短期需要等待确认。",
        "risk_warning": "RSI超买。",
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": "bullish",
                    "expected_return_pct": 3.0,
                    "rationale": "中期趋势仍向上。",
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
                "watch_conditions": ["回踩后能否获得支撑"],
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


def test_legacy_final_decision_derivers_are_removed() -> None:
    assert not hasattr(unified_report, "_final_action")
    assert not hasattr(unified_report, "_agreement")
    assert not hasattr(unified_report, "_decision_balance_lines")
    assert not hasattr(unified_report, "_has_active_trade_plan")


def test_report_action_agreement_and_decision_block_come_from_packet() -> None:
    payload = _payload()
    records = [_record()]
    packet = build_final_decision_packets(payload, v4_records=records)[0]
    report = unified_report.render_integrated_chinese_report(
        payload,
        v4_records=records,
        report_date="2026-08-11",
    )

    assert f"最终：{action_label_zh(packet)} · {agreement_label_zh(packet)}" in report
    for line in render_final_decision_lines(packet):
        assert line in report


def test_v4_normalization_compatibility_alias_uses_canonical_adapter() -> None:
    records = [_record()]
    assert unified_report._latest_v4_views(records) == unified_report.latest_v4_views(records)
