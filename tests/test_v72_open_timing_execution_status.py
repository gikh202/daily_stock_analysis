from __future__ import annotations

from scripts.run_us_open_confirmation import ConfirmationDecision
from scripts.run_us_open_timing import _effective_timing_base, _execution_contract


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


def test_rejected_contract_remains_hard_no_buy():
    packet = {"assessment": {"execution_status": "REJECTED"}}
    base = _base(reason="上一收盘执行状态为 REJECTED；风险层明确禁止建立新仓。")
    status, reason = _effective_timing_base(packet, base)
    assert status == "NO_BUY"
    assert "REJECTED" in reason
