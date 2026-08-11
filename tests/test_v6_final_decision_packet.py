from __future__ import annotations

from src.v6_daily.fusion_contracts import (
    FINAL_ASSESSMENT_SCOPE,
    FINAL_DECISION_PACKET_SCHEMA_VERSION,
    FinalVerdict,
    FusionAgreement,
    action_label_zh,
    build_final_decision_packet,
    render_final_decision_lines,
)


def _v6(
    *,
    decision: str = "WATCH",
    direction: str = "bullish",
    risk: float = 47.1,
    active_plan: bool = True,
) -> dict:
    return {
        "code": "MSFT",
        "instrument_type": "STOCK",
        "effective_trade_date": "2026-08-10",
        "decision": decision,
        "direction": direction,
        "forecast_score": 83.5,
        "opportunity_score": 77.7,
        "risk_score": risk,
        "evidence_coverage": 0.77,
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
    }


def _v4(
    *,
    direction: str = "bullish",
    operation: str = "观望",
    phase: str = "premarket",
    is_trading_day: bool = True,
) -> dict:
    return {
        "code": "MSFT",
        "operation": operation,
        "trend_prediction": direction,
        "forecast": {
            "horizon": "10d",
            "direction": direction,
            "expected_return_pct": 3.0,
            "rationale": "中期趋势仍向上，但短线需要消化超买。",
        },
        "strongest_bullish": "多头排列且相对强弱领先",
        "strongest_bearish": "RSI超买且上涨缩量",
        "catalysts": ["财报后云与AI业务增长继续提供支撑"],
        "risks": ["短线超买和资本开支压力可能引发回调"],
        "risk_warning": "RSI超买且消息面存在法律与资本开支风险。",
        "watch_conditions": [
            "回踩MA5后能否获得支撑",
            "上涨时量能是否放大",
        ],
        "phase": phase,
        "is_trading_day": is_trading_day,
        "immediate_action": "等待盘中确认，不追高",
    }


def test_watch_active_plan_becomes_final_conditional_buy_only_after_fusion() -> None:
    packet = build_final_decision_packet(_v6(), _v4())

    assert packet.schema_version == FINAL_DECISION_PACKET_SCHEMA_VERSION
    assert packet.assessment.scope == FINAL_ASSESSMENT_SCOPE
    assert packet.assessment.is_final is True
    assert packet.fusion_complete is True
    assert packet.agreement == FusionAgreement.ALIGNED
    assert packet.assessment.verdict == FinalVerdict.CONDITIONAL_BUY
    assert packet.assessment.worth_buying is True
    assert packet.assessment.execution_authorized is False
    assert packet.execution.has_active_plan is True
    assert action_label_zh(packet) == "观察"
    assert "多头排列且相对强弱领先" in packet.assessment.bullish_evidence
    assert "RSI超买且上涨缩量" in packet.assessment.bearish_evidence

    rendered = "\n".join(render_final_decision_lines(packet))
    assert "**是否值得买**：**条件式可买**" in rendered
    assert "**支持买入的证据**" in rendered
    assert "**支持等待/不买的证据**" in rendered
    assert "**关键分界**" in rendered
    assert "最大仓位上限 5.0%" in rendered


def test_direction_conflict_blocks_conditional_buy() -> None:
    packet = build_final_decision_packet(
        _v6(direction="bearish"),
        _v4(direction="bullish"),
    )

    assert packet.agreement == FusionAgreement.CONFLICT
    assert packet.assessment.verdict == FinalVerdict.WATCH
    assert packet.assessment.worth_buying is None
    assert packet.assessment.execution_authorized is False
    rendered = "\n".join(render_final_decision_lines(packet))
    assert "**是否值得买**：**继续观察**" in rendered
    assert "多空方向存在直接分歧" in rendered
    assert "条件式可买" not in rendered


def test_high_risk_blocks_conditional_buy() -> None:
    packet = build_final_decision_packet(_v6(risk=82.0), _v4())

    assert packet.assessment.verdict == FinalVerdict.WATCH
    assert packet.assessment.worth_buying is None
    assert packet.assessment.execution_authorized is False
    rendered = "\n".join(render_final_decision_lines(packet))
    assert "当前风险分偏高或已不低于机会分" in rendered


def test_incomplete_plan_blocks_watch_promotion() -> None:
    packet = build_final_decision_packet(
        _v6(active_plan=False),
        _v4(),
    )

    assert packet.execution.has_active_plan is False
    assert packet.assessment.verdict == FinalVerdict.WATCH
    assert packet.assessment.worth_buying is None
    assert packet.assessment.execution_authorized is False
    rendered = "\n".join(render_final_decision_lines(packet))
    assert "没有活动确定性交易计划" in rendered


def test_v4_sell_guidance_blocks_watch_promotion_even_when_v6_is_bullish() -> None:
    packet = build_final_decision_packet(
        _v6(),
        _v4(operation="卖出"),
    )

    assert packet.assessment.verdict == FinalVerdict.WATCH
    assert packet.assessment.worth_buying is None
    assert packet.assessment.execution_authorized is False


def test_wait_and_avoid_keep_both_evidence_sides() -> None:
    wait_packet = build_final_decision_packet(_v6(decision="WAIT", active_plan=False), _v4())
    avoid_packet = build_final_decision_packet(
        _v6(decision="AVOID", direction="bearish", active_plan=False),
        _v4(),
    )

    assert wait_packet.assessment.verdict == FinalVerdict.WAIT
    assert wait_packet.assessment.worth_buying is False
    assert wait_packet.assessment.bullish_evidence
    assert wait_packet.assessment.bearish_evidence
    assert "暂不买，等待确认" in "\n".join(render_final_decision_lines(wait_packet))

    assert avoid_packet.assessment.verdict == FinalVerdict.AVOID
    assert avoid_packet.assessment.worth_buying is False
    assert avoid_packet.assessment.bullish_evidence
    assert avoid_packet.assessment.bearish_evidence
    assert "当前不买/回避" in "\n".join(render_final_decision_lines(avoid_packet))


def test_missing_v4_never_becomes_fake_final_buy_verdict() -> None:
    packet = build_final_decision_packet(_v6(), None)

    assert packet.fusion_complete is False
    assert packet.agreement == FusionAgreement.V4_MISSING
    assert packet.assessment.is_final is False
    assert packet.assessment.verdict == FinalVerdict.DATA_INCOMPLETE
    assert packet.assessment.worth_buying is None
    assert packet.assessment.execution_authorized is False
    assert "不能形成最终买入判断" in "\n".join(render_final_decision_lines(packet))


def test_non_trading_can_preserve_buyable_thesis_without_authorizing_execution() -> None:
    packet = build_final_decision_packet(
        _v6(),
        _v4(phase="closed", is_trading_day=False),
    )

    assert packet.assessment.verdict == FinalVerdict.CONDITIONAL_BUY
    assert packet.assessment.worth_buying is True
    assert packet.assessment.execution_authorized is False
    assert action_label_zh(packet) == "观察（等待开盘确认）"


def test_buy_setup_requires_complete_plan_and_final_execution_context() -> None:
    ready = build_final_decision_packet(
        _v6(decision="BUY_SETUP"),
        _v4(operation="买入"),
    )
    observe = build_final_decision_packet(
        _v6(decision="BUY_SETUP"),
        _v4(operation="观望"),
    )
    blocked_plan = build_final_decision_packet(
        _v6(decision="BUY_SETUP", active_plan=False),
        _v4(operation="买入"),
    )

    assert ready.assessment.verdict == FinalVerdict.BUY_BY_PLAN
    assert ready.assessment.execution_authorized is True
    assert action_label_zh(ready) == "买入准备"

    assert observe.assessment.verdict == FinalVerdict.WATCH
    assert observe.assessment.worth_buying is True
    assert observe.assessment.execution_authorized is False

    assert blocked_plan.assessment.verdict == FinalVerdict.WATCH
    assert blocked_plan.assessment.execution_authorized is False
