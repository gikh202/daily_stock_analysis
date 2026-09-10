from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.run_us_open_confirmation import (
    LiveSnapshot,
    _validated_live_price,
    classify_confirmation,
    render_markdown,
)


NY = ZoneInfo("America/New_York")


def _packet(
    *,
    verdict: str = "conditional_buy",
    worth_buying: bool | None = True,
    authorized: bool = False,
    entry=(100.0, 105.0),
    stop=96.0,
    targets=(112.0, 120.0),
    max_position=0.2,
):
    return {
        "identity": {
            "symbol": "TEST",
            "instrument_type": "STOCK",
            "effective_trade_date": "2026-08-13",
        },
        "assessment": {
            "verdict": verdict,
            "worth_buying": worth_buying,
            "execution_authorized": authorized,
        },
        "execution": {
            "entry_zone": list(entry) if entry else None,
            "stop_loss": stop,
            "targets": list(targets),
            "max_position_pct": max_position,
            "confirmations": ["价格进入计划区间", "量价不明显走弱"],
            "has_active_plan": bool(entry and stop and targets and max_position),
        },
    }


def _snapshot(
    price: float,
    *,
    open_price: float = 102.0,
    return_from_open_pct: float | None = None,
    volume_ratio: float | None = 1.0,
):
    return LiveSnapshot(
        symbol="TEST",
        current_price=price,
        session_open=open_price,
        session_high=max(price, open_price),
        session_low=min(price, open_price),
        opening_15m_high=max(price, open_price),
        opening_15m_low=min(price, open_price),
        return_from_open_pct=(
            (price / open_price - 1.0) * 100.0
            if return_from_open_pct is None
            else return_from_open_pct
        ),
        opening_15m_volume=1_000_000,
        recent_opening_volume_median=900_000,
        volume_ratio=volume_ratio,
        bar_count=15,
        last_bar_time="2026-08-14T09:44:00-04:00",
    )


def test_buy_now_when_price_is_in_plan_and_open_is_not_weak():
    decision = classify_confirmation(_packet(), _snapshot(103.0))
    assert decision.status == "BUY_NOW"
    assert decision.starter_position_pct == pytest.approx(10.0)
    assert decision.max_position_pct == pytest.approx(20.0)


def test_starter_position_never_exceeds_plan_cap():
    decision = classify_confirmation(
        _packet(max_position=0.05),
        _snapshot(103.0),
        starter_position_pct=10.0,
    )
    assert decision.status == "BUY_NOW"
    assert decision.starter_position_pct == pytest.approx(5.0)


def test_below_entry_waits_instead_of_catching_falling_price():
    decision = classify_confirmation(_packet(), _snapshot(99.0, open_price=101.0))
    assert decision.status == "WAIT_ENTRY"
    assert decision.starter_position_pct == 0.0


def test_price_above_chase_limit_does_not_authorize_buy():
    decision = classify_confirmation(
        _packet(),
        _snapshot(106.0, open_price=104.0),
        chase_tolerance_pct=0.5,
    )
    assert decision.status == "WAIT_PULLBACK"


def test_stop_breach_invalidates_prior_plan():
    decision = classify_confirmation(_packet(), _snapshot(95.5, open_price=98.0))
    assert decision.status == "INVALIDATED"


def test_weak_open_blocks_buy_even_inside_entry_zone():
    decision = classify_confirmation(
        _packet(),
        _snapshot(101.0, open_price=102.0, return_from_open_pct=-0.98),
    )
    assert decision.status == "WAIT_STABILIZE"


def test_weak_volume_blocks_buy_when_price_is_not_positive():
    decision = classify_confirmation(
        _packet(),
        _snapshot(102.0, open_price=102.2, return_from_open_pct=-0.20, volume_ratio=0.4),
    )
    assert decision.status == "WAIT_STABILIZE"


@pytest.mark.parametrize("verdict", ["avoid", "wait", "data_incomplete"])
def test_prior_close_verdict_is_context_when_live_plan_is_executable(verdict):
    decision = classify_confirmation(
        _packet(verdict=verdict, worth_buying=False),
        _snapshot(103.0),
    )
    assert decision.status == "BUY_NOW"
    assert "仅作为历史风险背景" in decision.reason


def test_buyable_watch_from_close_can_be_confirmed_intraday():
    decision = classify_confirmation(
        _packet(verdict="watch", worth_buying=True),
        _snapshot(103.0),
    )
    assert decision.status == "BUY_NOW"


def test_missing_trade_plan_never_gets_invented_intraday():
    decision = classify_confirmation(
        _packet(entry=None),
        _snapshot(103.0),
    )
    assert decision.status == "NO_BUY"


def test_missing_live_data_is_explicitly_non_actionable():
    decision = classify_confirmation(
        _packet(),
        None,
        data_error="no regular-session bars",
    )
    assert decision.status == "DATA_UNAVAILABLE"
    assert decision.current_price is None


def test_report_makes_now_action_unambiguous():
    buy = classify_confirmation(_packet(), _snapshot(103.0))
    wait = classify_confirmation(_packet(), _snapshot(106.0), chase_tolerance_pct=0.5)
    report = render_markdown(
        [buy, wait],
        generated_at=datetime(2026, 8, 14, 9, 45, tzinfo=NY),
        source_run_id="123456",
    )
    assert "这封邮件只回答“现在买不买”" in report
    assert "可以买（首仓）" in report
    assert "不追，等回踩" in report
    assert "首仓不超过 10.0%" in report
    assert "V6 run `123456`" in report



class _FastInfo:
    def __init__(self, *, last_price=None, day_low=None, day_high=None):
        self.last_price = last_price
        self.day_low = day_low
        self.day_high = day_high


class _Ticker:
    def __init__(self, info):
        self.fast_info = info


def _session(closes):
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [value + 0.2 for value in closes],
            "Low": [value - 0.2 for value in closes],
            "Close": closes,
            "Volume": [1000] * len(closes),
        }
    )


def test_live_price_rejects_last_bar_outside_quote_day_range():
    ticker = _Ticker(_FastInfo(last_price=335.40, day_low=333.22, day_high=339.67))
    price, source, bar, quote, validation, low, high = _validated_live_price(
        ticker,
        _session([335.1, 335.3, 335.2, 335.4, 335.5, 329.92]),
        "GOOGL",
    )
    assert price == pytest.approx(335.40)
    assert source == "yfinance_fast_info"
    assert bar == pytest.approx(329.92)
    assert quote == pytest.approx(335.40)
    assert validation == "bar_outside_quote_day_range"
    assert low == pytest.approx(333.22)
    assert high == pytest.approx(339.67)


def test_live_price_prefers_fresh_quote_when_bar_and_quote_agree():
    ticker = _Ticker(_FastInfo(last_price=491.25, day_low=490.15, day_high=495.19))
    price, source, bar, quote, validation, _, _ = _validated_live_price(
        ticker,
        _session([491.0, 491.1, 491.15, 491.2, 491.22, 491.07]),
        "MSFT",
    )
    assert price == pytest.approx(491.25)
    assert source == "yfinance_fast_info"
    assert bar == pytest.approx(491.07)
    assert quote == pytest.approx(491.25)
    assert validation == "cross_checked"


def test_live_price_fails_closed_on_unresolved_large_source_conflict():
    ticker = _Ticker(_FastInfo(last_price=105.0))
    with pytest.raises(RuntimeError, match="conflicting live prices"):
        _validated_live_price(
            ticker,
            _session([100.0, 100.1, 100.0, 100.1, 100.0, 95.0]),
            "TEST",
        )


def test_live_price_without_quote_rejects_isolated_outlier():
    ticker = _Ticker(None)
    with pytest.raises(RuntimeError, match="unvalidated 1m price outlier"):
        _validated_live_price(
            ticker,
            _session([100.0, 100.1, 100.0, 100.1, 100.0, 96.0]),
            "TEST",
        )
