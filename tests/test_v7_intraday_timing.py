from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yfinance as yf

from scripts.run_us_open_confirmation import LiveSnapshot
from scripts.run_us_open_timing import _extended_intraday
from src.forecasting.timing import IntradayTimingModel

NY = ZoneInfo("America/New_York")


def test_timing_waits_when_pullback_probability_has_material_value() -> None:
    decision=IntradayTimingModel().assess(base_status="BUY_NOW",current_price=105.0,entry_low=100.0,entry_high=105.0,stop_loss=96.0,session_low=101.0,session_high=105.2,session_vwap=103.5,last_5m_return_pct=-.25,intraday_volatility_pct=1.2,minutes_since_open=25,probability_up_1d=.54,probability_up_5d=.61)
    assert decision.action=="WAIT_BETTER_ENTRY" and decision.better_entry_probability>=.62 and decision.expected_better_price<105.0 and decision.terminal is False
    assert decision.expected_wait_minutes == 30
    assert decision.better_entry_reason == "atr_pullback"


def test_strong_continuation_can_keep_buy_now() -> None:
    decision=IntradayTimingModel().assess(base_status="BUY_NOW",current_price=103.5,entry_low=101.0,entry_high=104.0,stop_loss=97.0,session_low=102.0,session_high=104.0,session_vwap=103.3,last_5m_return_pct=.45,intraday_volatility_pct=.5,minutes_since_open=35,probability_up_1d=.64,probability_up_5d=.68)
    assert decision.action=="BUY_NOW" and decision.terminal is True
    assert decision.expected_wait_minutes == 0
    assert decision.better_entry_reason is None


def test_hard_blocker_cannot_be_resurrected_by_timing_model() -> None:
    decision=IntradayTimingModel().assess(base_status="INVALIDATED",current_price=95.0,entry_low=100.0,entry_high=105.0,stop_loss=96.0,session_low=94.0,session_high=103.0,session_vwap=99.0,last_5m_return_pct=1.0,intraday_volatility_pct=2.0,minutes_since_open=40,probability_up_1d=.8,probability_up_5d=.8)
    assert decision.action=="INVALIDATED" and decision.terminal is True


def test_extended_intraday_does_not_peek_after_signal_bar(monkeypatch) -> None:
    timestamps = [pd.Timestamp(f"2026-08-14 09:{minute:02d}", tz="America/New_York") for minute in range(30, 46)]
    rows = []
    for ts in timestamps:
        if ts.minute <= 44:
            rows.append((100.0, 100.1, 99.9, 100.0, 1000))
        else:
            rows.append((100.0, 120.0, 50.0, 50.0, 999999))
    frame = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(timestamps))

    class FakeTicker:
        def history(self, **kwargs):
            return frame.copy()

    monkeypatch.setattr(yf, "Ticker", lambda symbol: FakeTicker())
    snapshot = LiveSnapshot(
        symbol="TEST",
        current_price=100.0,
        session_open=100.0,
        session_high=100.1,
        session_low=99.9,
        opening_15m_high=100.1,
        opening_15m_low=99.9,
        return_from_open_pct=0.0,
        opening_15m_volume=15000.0,
        recent_opening_volume_median=15000.0,
        volume_ratio=1.0,
        bar_count=15,
        last_bar_time="2026-08-14T09:44:00-04:00",
    )

    fields = _extended_intraday(
        "TEST",
        datetime(2026, 8, 14, 9, 45, tzinfo=NY),
        snapshot,
    )
    assert fields["session_vwap"] == pytest.approx(100.0)
    assert fields["last_5m_return_pct"] == pytest.approx(0.0)
    assert fields["intraday_volatility_pct"] == pytest.approx(0.0)
