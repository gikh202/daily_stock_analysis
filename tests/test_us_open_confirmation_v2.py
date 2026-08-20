from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.run_us_open_confirmation import LiveSnapshot
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2


NY = ZoneInfo("America/New_York")


def _packet(*, entry=(100.0, 105.0), trade_date="2026-08-13"):
    return {
        "identity": {
            "symbol": "TEST",
            "instrument_type": "STOCK",
            "effective_trade_date": trade_date,
        },
        "assessment": {
            "verdict": "conditional_buy",
            "worth_buying": True,
            "execution_authorized": False,
        },
        "execution": {
            "entry_zone": list(entry),
            "stop_loss": 96.0,
            "targets": [112.0, 120.0],
            "max_position_pct": 0.2,
            "confirmations": ["价格进入计划区间"],
            "has_active_plan": True,
        },
    }


def _snapshot(
    price: float,
    *,
    open_price: float = 103.0,
    opening_low: float = 101.0,
    opening_high: float = 106.0,
    volume_ratio: float | None = 1.0,
    last_bar="2026-08-14T09:44:00-04:00",
    bar_count: int = 15,
):
    return LiveSnapshot(
        symbol="TEST",
        current_price=price,
        session_open=open_price,
        session_high=opening_high,
        session_low=opening_low,
        opening_15m_high=opening_high,
        opening_15m_low=opening_low,
        return_from_open_pct=(price / open_price - 1.0) * 100.0,
        opening_15m_volume=1_000_000,
        recent_opening_volume_median=900_000,
        volume_ratio=volume_ratio,
        bar_count=bar_count,
        last_bar_time=last_bar,
    )


def _at(hour=9, minute=45):
    return datetime(2026, 8, 14, hour, minute, tzinfo=NY)


def test_normal_in_zone_buy_survives_v2_guards():
    decision = classify_confirmation_v2(_packet(), _snapshot(104.0), evaluated_at=_at())
    assert decision.status == "BUY_NOW"


def test_low_opening_range_position_blocks_buy():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(102.0, open_price=102.1, opening_low=101.5, opening_high=105.0),
        evaluated_at=_at(),
    )
    assert decision.status == "WAIT_STABILIZE"
    assert "开盘确认区间" in decision.reason
    assert "09:45" not in decision.reason


def test_momentum_extension_allows_only_confirmed_strength():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(105.6, open_price=104.0, opening_low=103.0, opening_high=106.0, volume_ratio=0.9),
        evaluated_at=_at(),
    )
    assert decision.status == "BUY_NOW"
    assert "动量扩展" in decision.reason


def test_momentum_extension_rejects_weak_volume():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(105.6, open_price=104.0, opening_low=103.0, opening_high=106.0, volume_ratio=0.5),
        evaluated_at=_at(),
    )
    assert decision.status == "WAIT_PULLBACK"


def test_never_extends_beyond_point_75_percent():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(105.9, open_price=104.0, opening_low=103.0, opening_high=106.0, volume_ratio=1.0),
        evaluated_at=_at(),
    )
    assert decision.status == "WAIT_PULLBACK"


def test_stale_quote_is_non_actionable():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(104.0, last_bar="2026-08-14T09:30:00-04:00"),
        evaluated_at=_at(),
    )
    assert decision.status == "DATA_UNAVAILABLE"
    assert decision.starter_position_pct == 0.0


def test_late_runtime_uses_current_quote_instead_of_fixed_cutoff():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(104.0, last_bar="2026-08-14T10:19:00-04:00", bar_count=50),
        evaluated_at=_at(10, 20),
    )
    assert decision.status == "BUY_NOW"
    assert decision.label != "确认已过时，暂不下单"


def test_pre_0945_partial_volume_ratio_cannot_create_false_weak_volume_block():
    decision = classify_confirmation_v2(
        _packet(),
        _snapshot(
            102.9,
            open_price=103.0,
            opening_low=102.7,
            opening_high=103.4,
            volume_ratio=0.2,
            last_bar="2026-08-14T09:36:00-04:00",
            bar_count=7,
        ),
        evaluated_at=_at(9, 37),
    )
    assert decision.status == "BUY_NOW"


def test_same_day_plan_is_rejected_as_not_prior_close_plan():
    decision = classify_confirmation_v2(
        _packet(trade_date="2026-08-14"),
        _snapshot(104.0),
        evaluated_at=_at(),
    )
    assert decision.status == "NO_BUY"


def test_weekend_friday_plan_is_valid_for_monday():
    packet = _packet(trade_date="2026-08-14")
    snapshot = _snapshot(104.0, last_bar="2026-08-17T09:44:00-04:00")
    evaluated = datetime(2026, 8, 17, 9, 45, tzinfo=NY)
    decision = classify_confirmation_v2(packet, snapshot, evaluated_at=evaluated)
    assert decision.status == "BUY_NOW"


def test_plan_older_than_four_calendar_days_is_rejected():
    packet = _packet(trade_date="2026-08-09")
    decision = classify_confirmation_v2(packet, _snapshot(104.0), evaluated_at=_at())
    assert decision.status == "NO_BUY"
