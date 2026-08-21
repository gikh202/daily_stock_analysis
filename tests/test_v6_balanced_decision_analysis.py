from __future__ import annotations

import json

from src.v6_daily.accuracy_report import build_investor_email_markdown
from src.v6_daily.unified_report import render_integrated_chinese_report


def _payload(*, decision: str = "WATCH", direction: str = "bullish", active_plan: bool = True) -> dict:
    trade_plan = {
        "entry_zone": [502.0, 506.0] if active_plan else None,
        "stop_loss": 487.0 if active_plan else None,
        "targets": [544.0, 563.0] if active_plan else None,
        "risk_reward": 2.0 if active_plan else None,
        "max_position_pct": 0.05 if active_plan else 0.0,
    }
    return {
        "version": "v6-test",
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
                "forecast_score": 83.5 if direction == "bullish" else 55.0,
                "opportunity_score": 77.7,
                "quality_score": 76.8,
                "risk_score": 47.1,
                "evidence_coverage": 0.77,
                "llm_health": "healthy",
                "features": {
                    "trend": 71,
                    "momentum": 98,
                    "relative_strength": 99,
                    "volume_confirmation": 35,
                    "fundamental_quality": 74,
                    "market_regime": 80,
                },
                "trade_plan": trade_plan,
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


def _v4_record(*, operation: str = "观望") -> dict:
    raw = {
        "name": "Microsoft Corporation",
        "analysis_summary": "微软中期逻辑偏多，建议逢回踩买入，仓位三成；但短期超买，需要等待确认。",
        "technical_analysis": "均线维持多头，但RSI进入超买区。",
        "fundamental_analysis": "云与AI业务增长仍是中期支撑。",
        "volume_analysis": "上涨缩量，突破需要放量确认。",
        "news_summary": "利好与风险事件同时存在。",
        "risk_warning": "RSI超买且消息面存在法律与资本开支风险。",
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": "bullish",
                    "up_probability": 65,
                    "expected_return_pct": 3.0,
                    "rationale": "中期趋势仍向上，但短线需要消化超买。",
                }
            },
        },
        "execution": {"operation_advice": operation, "action": "watch"},
        "dashboard": {
            "core_conclusion": {
                "one_sentence": "趋势偏多但不适合无条件追价。",
                "position_advice": {},
            },
            "intelligence": {
                "earnings_outlook": "云与AI业务保持增长。",
                "sentiment_summary": "市场情绪偏多但存在风险杂音。",
                "latest_news": "近期事件对基本面有支撑也有扰动。",
                "positive_catalysts": ["财报后云与AI业务增长继续提供支撑"],
                "risk_alerts": ["短线超买和资本开支压力可能引发回调"],
            },
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "502-506美元",
                    "stop_loss": "487美元",
                    "take_profit": "544-563美元",
                },
                "position_strategy": {
                    "suggested_position": "三成",
                    "risk_control": "若跌破关键支撑则退出",
                },
            },
            "phase_decision": {
                "watch_conditions": [
                    "回踩MA5后能否获得支撑",
                    "上涨时量能是否放大",
                    "若放量跌破关键支撑则取消买入计划",
                ],
                "next_check_time": "2026-08-11 09:30 ET",
                "immediate_action": "等待盘中确认，不追高",
                "data_limitations": [],
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


def test_balanced_analysis_keeps_both_sides_and_original_opinion() -> None:
    report = render_integrated_chinese_report(
        _payload(decision="WATCH", direction="bullish", active_plan=True),
        v4_records=[_v4_record(operation="观望")],
        report_date="2026-08-11",
    )

    assert "**是否值得买**：**条件式可买**" in report
    assert "**支持买入的证据**" in report
    assert "多头排列且相对强弱领先" in report
    assert "财报后云与AI业务增长继续提供支撑" in report
    assert "**支持等待/不买的证据**" in report
    assert "RSI超买且上涨缩量" in report
    assert "短线超买和资本开支压力可能引发回调" in report
    assert "**关键分界**" in report
    assert "回踩MA5后能否获得支撑" in report
    assert "V4 投研摘要（原始观点）" in report
    assert "建议逢回踩买入，仓位三成" in report

    email = build_investor_email_markdown(report)
    assert "**是否值得买**：**条件式可买**" in email
    assert "**支持买入的证据**" in email
    assert "**支持等待/不买的证据**" in email
    assert "上游投研摘要（仅解释，非执行）" in email
    assert "投研摘要（原始观点）" not in email
    assert "建议逢回踩买入，仓位三成" in email
    assert "确定性风控计划为唯一执行价格口径" in email
    assert "最大仓位上限" in email


def test_wait_keeps_bull_case_as_conditional_but_not_executable_yet() -> None:
    report = render_integrated_chinese_report(
        _payload(decision="WAIT", direction="neutral", active_plan=False),
        v4_records=[_v4_record(operation="观望")],
        report_date="2026-08-11",
    )

    # This raw fusion renderer owns verdict/evidence prose only. Final execution
    # authorization and executable-position lines are injected and validated by
    # final_decision_renderer.apply_final_decision_contract().
    assert "**是否值得买**：**条件式可买**" in report
    assert "多头排列且相对强弱领先" in report
    assert "RSI超买且上涨缩量" in report
    assert "看多逻辑仍保留" in report
    assert "方向尚未形成完全共振" in report


def test_avoid_does_not_erase_existing_bullish_evidence() -> None:
    report = render_integrated_chinese_report(
        _payload(decision="AVOID", direction="bearish", active_plan=False),
        v4_records=[_v4_record(operation="观望")],
        report_date="2026-08-11",
    )

    assert "**是否值得买**：**当前不买/回避**" in report
    assert "多头排列且相对强弱领先" in report
    assert "RSI超买且上涨缩量" in report
    assert "即使存在局部看多证据" in report


def test_watch_with_direction_conflict_stays_observation() -> None:
    report = render_integrated_chinese_report(
        _payload(decision="WATCH", direction="bearish", active_plan=True),
        v4_records=[_v4_record(operation="观望")],
        report_date="2026-08-11",
    )

    assert "方向分歧" in report
    assert "**是否值得买**：**继续观察**" in report
    assert "多空方向存在直接分歧" in report
    assert "**是否值得买**：**条件式可买**" not in report
    assert "多头排列且相对强弱领先" in report
    assert "RSI超买且上涨缩量" in report


def test_watch_with_high_risk_stays_observation() -> None:
    payload = _payload(decision="WATCH", direction="bullish", active_plan=True)
    payload["board"][0]["risk_score"] = 82.0

    report = render_integrated_chinese_report(
        payload,
        v4_records=[_v4_record(operation="观望")],
        report_date="2026-08-11",
    )

    assert "**是否值得买**：**继续观察**" in report
    assert "当前风险分偏高或已不低于机会分" in report
    assert "**是否值得买**：**条件式可买**" not in report
    assert "多头排列且相对强弱领先" in report
    assert "RSI超买且上涨缩量" in report
