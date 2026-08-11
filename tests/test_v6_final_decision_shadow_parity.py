from __future__ import annotations

import json

from src.v6_daily.final_decision_service import build_final_decision_packets
from src.v6_daily.fusion_contracts import (
    FinalVerdict,
    agreement_label_zh,
    render_final_decision_lines,
    verdict_label_zh,
)
from src.v6_daily.unified_report import _latest_v4_views, render_integrated_chinese_report
from src.v6_daily.v4_research_adapter import latest_v4_views


def _payload(*, decision: str = "WATCH", direction: str = "bullish", risk: float = 47.1, active_plan: bool = True) -> dict:
    return {
        "version": "v6-shadow-test",
        "generated_at": "2026-08-11T00:00:00Z",
        "market_pulse": {
            "regime": "risk_on",
            "breadth": "neutral",
            "average_opportunity": 67.0,
            "average_risk": 42.0,
            "average_evidence_coverage": 0.8,
        },
        "board": [
            {
                "code": "MSFT",
                "instrument_type": "STOCK",
                "decision": decision,
                "direction": direction,
                "forecast_score": 83.5,
                "opportunity_score": 77.7,
                "quality_score": 76.8,
                "risk_score": risk,
                "evidence_coverage": 0.77,
                "llm_health": "healthy",
                "features": {},
                "trade_plan": {
                    "action": decision,
                    "entry_zone": [502.0, 506.0] if active_plan else None,
                    "stop_loss": 487.0 if active_plan else None,
                    "targets": [544.0, 563.0] if active_plan else None,
                    "risk_reward": 2.0 if active_plan else None,
                    "max_position_pct": 0.05 if active_plan else 0.0,
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


def _record(*, direction: str = "bullish", operation: str = "观望") -> dict:
    raw = {
        "name": "Microsoft Corporation",
        "analysis_summary": "微软中期逻辑偏多，建议逢回踩买入；但短期超买，需要等待确认。",
        "risk_warning": "RSI超买且消息面存在法律与资本开支风险。",
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": direction,
                    "up_probability": 65,
                    "expected_return_pct": 3.0,
                    "rationale": "中期趋势仍向上，但短线需要消化超买。",
                }
            },
        },
        "execution": {"operation_advice": operation, "action": "watch"},
        "dashboard": {
            "core_conclusion": {"one_sentence": "趋势偏多但不适合无条件追价。"},
            "intelligence": {
                "positive_catalysts": ["财报后云与AI业务增长继续提供支撑"],
                "risk_alerts": ["短线超买和资本开支压力可能引发回调"],
            },
            "battle_plan": {},
            "phase_decision": {
                "watch_conditions": ["回踩MA5后能否获得支撑", "上涨时量能是否放大"],
                "immediate_action": "等待盘中确认，不追高",
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


def _assert_shadow_matches_report(payload: dict, record: dict, expected: FinalVerdict) -> None:
    packet = build_final_decision_packets(payload, v4_records=[record])[0]
    report = render_integrated_chinese_report(payload, v4_records=[record], report_date="2026-08-11")

    assert packet.assessment.verdict == expected
    assert f"**是否值得买**：**{verdict_label_zh(packet)}**" in report
    assert agreement_label_zh(packet) in report
    for evidence in packet.assessment.bullish_evidence[:2]:
        assert evidence in report
    for evidence in packet.assessment.bearish_evidence[:2]:
        assert evidence in report
    assert "\n".join(render_final_decision_lines(packet)).splitlines()[0] in report


def test_new_v4_adapter_is_byte_for_byte_equivalent_to_legacy_normalized_view() -> None:
    records = [_record()]
    assert latest_v4_views(records) == _latest_v4_views(records)


def test_watch_conditional_buy_shadow_matches_current_renderer() -> None:
    _assert_shadow_matches_report(_payload(), _record(), FinalVerdict.CONDITIONAL_BUY)


def test_direction_conflict_shadow_matches_current_renderer() -> None:
    _assert_shadow_matches_report(
        _payload(direction="bearish"),
        _record(direction="bullish"),
        FinalVerdict.WATCH,
    )


def test_high_risk_shadow_matches_current_renderer() -> None:
    _assert_shadow_matches_report(
        _payload(risk=82.0),
        _record(),
        FinalVerdict.WATCH,
    )


def test_wait_shadow_matches_current_renderer() -> None:
    _assert_shadow_matches_report(
        _payload(decision="WAIT", direction="neutral", active_plan=False),
        _record(),
        FinalVerdict.WAIT,
    )


def test_avoid_shadow_matches_current_renderer() -> None:
    _assert_shadow_matches_report(
        _payload(decision="AVOID", direction="bearish", active_plan=False),
        _record(),
        FinalVerdict.AVOID,
    )
