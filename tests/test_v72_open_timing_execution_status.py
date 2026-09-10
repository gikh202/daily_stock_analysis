from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.run_us_open_confirmation import (
    ConfirmationDecision,
    LiveSnapshot,
    classify_confirmation,
)
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2
from scripts.run_us_open_timing import (
    OpenTimingDecision,
    _effective_timing_base,
    _enforce_execution_contract,
    _execution_contract,
)


def _base(*, reason: str, status: str = "NO_BUY") -> ConfirmationDecision:
    return ConfirmationDecision(
        symbol="TEST",
        status=status,
        label="今天不买",
        reason=reason,
        current_price=100.0,
        entry_low=None,
        entry_high=None,
        stop_loss=None,
        targets=(),
        starter_position_pct=0.0,
        max_position_pct=0.0,
        return_from_open_pct=0.0,
        volume_ratio=1.0,
        prior_verdict="conditional_buy",
        prior_worth_buying=True,
        prior_execution_authorized=False,
        prior_confirmations=(),
        source_trade_date="2026-08-20",
        source_last_bar_time="2026-08-21T09:45:00-04:00",
    )


def test_explicit_conditional_contract_is_read_from_close_packet():
    packet = {
        "assessment": {
            "execution_status": "CONDITIONAL_APPROVED",
            "execution_authorized": False,
            "worth_buying": True,
            "conditional_entry_price": 98.5,
            "conditional_entry_reason": "valuation_wait",
        }
    }
    contract = _execution_contract(packet)
    assert contract["status"] == "CONDITIONAL_APPROVED"
    assert contract["conditional_entry_price"] == 98.5
    assert contract["conditional_entry_reason"] == "valuation_wait"


def test_missing_plan_under_conditional_approval_becomes_wait_not_hard_no_buy():
    packet = {"assessment": {"execution_status": "CONDITIONAL_APPROVED"}}
    base = _base(
        reason="虽然逻辑偏多，但缺少完整入场区间、止损、目标或仓位上限，禁止临盘补造计划。"
    )
    status, reason = _effective_timing_base(packet, base)
    assert status == "WAIT_STABILIZE"
    assert "继续等待确认" in reason
    assert "不得临时补造" in reason


def test_conditional_approval_does_not_override_stale_plan_hard_blocker():
    packet = {"assessment": {"execution_status": "CONDITIONAL_APPROVED"}}
    base = _base(reason="上一计划日期过旧，不满足前收盘计划时效要求。")
    status, reason = _effective_timing_base(packet, base)
    assert status == "NO_BUY"
    assert "时效" in reason


def _live_snapshot() -> LiveSnapshot:
    return LiveSnapshot(
        symbol="TEST",
        current_price=100.0,
        session_open=100.0,
        session_high=101.0,
        session_low=99.0,
        opening_15m_high=101.0,
        opening_15m_low=99.0,
        return_from_open_pct=0.0,
        opening_15m_volume=1000.0,
        recent_opening_volume_median=1000.0,
        volume_ratio=1.0,
        bar_count=15,
        last_bar_time="2026-09-09T09:44:00-04:00",
    )


def _rejected_but_complete_packet() -> dict:
    return {
        "identity": {
            "symbol": "TEST",
            "effective_trade_date": "2026-09-08",
        },
        "assessment": {
            "execution_status": "REJECTED",
            "execution_authorized": False,
            "worth_buying": False,
            "verdict": "avoid",
        },
        "execution": {
            "entry_zone": [99.0, 101.0],
            "stop_loss": 95.0,
            "targets": [110.0],
            "max_position_pct": 0.20,
            "has_active_plan": True,
            "confirmations": [],
        },
    }


def test_rejected_close_status_is_context_not_v1_open_veto():
    decision = classify_confirmation(
        _rejected_but_complete_packet(),
        _live_snapshot(),
    )
    assert decision.status == "BUY_NOW"
    assert "仅作为历史风险背景" in decision.reason


def test_legacy_rejected_close_status_maps_to_unresolved_not_v2_open_veto():
    packet = _rejected_but_complete_packet()
    contract = _execution_contract(packet)
    assert contract["status"] == "UNRESOLVED"
    assert contract["hard_block"] is False

    decision = classify_confirmation_v2(
        packet,
        _live_snapshot(),
        evaluated_at=datetime(
            2026,
            9,
            9,
            9,
            45,
            tzinfo=ZoneInfo("America/New_York"),
        ),
    )
    assert decision.status == "BUY_NOW"
    assert "未决状态" in decision.reason
    assert "实时行情重新确认" in decision.reason


def _timing_marker() -> OpenTimingDecision:
    return OpenTimingDecision(
        symbol="TEST",
        action="WAIT_CONFIRMATION",
        label="等确认再买",
        reason="test",
        current_price=100.0,
        entry_low=99.0,
        entry_high=101.0,
        stop_loss=95.0,
        targets=(110.0,),
        starter_position_pct=0.0,
        max_position_pct=10.0,
        return_from_open_pct=0.0,
        volume_ratio=1.0,
        probability_up_1d=None,
        probability_up_5d=None,
        probability_up_20d=None,
        expected_return_5d_pct=None,
        expected_alpha_5d_pct=None,
        forecast_confidence=None,
        better_entry_score=0.0,
        better_entry_probability=0.0,
        expected_better_price=None,
        expected_improvement_pct=0.0,
        recheck_minutes=15,
        terminal=False,
        source_trade_date="2026-09-08",
        source_last_bar_time="2026-09-09T09:44:00-04:00",
        execution_status="UNRESOLVED",
    )


def test_final_execution_contract_leaves_non_hard_live_action_unchanged():
    marker = _timing_marker()
    assert _enforce_execution_contract(marker) is marker
