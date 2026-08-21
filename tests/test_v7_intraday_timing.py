from __future__ import annotations

import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from scripts import run_us_open_timing as timing_module
from scripts.capture_us_open_research import reconstruct_snapshot_from_frame
from scripts.run_us_open_confirmation import LiveSnapshot
from scripts.run_us_open_confirmation_safe import _apply_wait_better_entry_contract
from src.forecasting.timing import IntradayTimingModel

NY=ZoneInfo("America/New_York")


def test_timing_waits_when_pullback_probability_has_material_value() -> None:
    decision=IntradayTimingModel().assess(base_status="BUY_NOW",current_price=105.0,entry_low=100.0,entry_high=105.0,stop_loss=96.0,session_low=101.0,session_high=105.2,session_vwap=103.5,last_5m_return_pct=-.25,intraday_volatility_pct=1.2,minutes_since_open=25,probability_up_1d=.54,probability_up_5d=.61)
    assert decision.action=="WAIT_BETTER_ENTRY" and decision.better_entry_probability>=.62 and decision.expected_better_price<105.0 and decision.terminal is False


def test_strong_continuation_can_keep_buy_now() -> None:
    decision=IntradayTimingModel().assess(base_status="BUY_NOW",current_price=103.5,entry_low=101.0,entry_high=104.0,stop_loss=97.0,session_low=102.0,session_high=104.0,session_vwap=103.3,last_5m_return_pct=.45,intraday_volatility_pct=.5,minutes_since_open=35,probability_up_1d=.64,probability_up_5d=.68)
    assert decision.action=="BUY_NOW" and decision.terminal is True


def test_hard_blocker_cannot_be_resurrected_by_timing_model() -> None:
    decision=IntradayTimingModel().assess(base_status="INVALIDATED",current_price=95.0,entry_low=100.0,entry_high=105.0,stop_loss=96.0,session_low=94.0,session_high=103.0,session_vwap=99.0,last_5m_return_pct=1.0,intraday_volatility_pct=2.0,minutes_since_open=40,probability_up_1d=.8,probability_up_5d=.8)
    assert decision.action=="INVALIDATED" and decision.terminal is True


def test_wait_output_contract_persists_settlement_metadata() -> None:
    payload={"decisions":[{"symbol":"TEST","action":"WAIT_BETTER_ENTRY","current_price":100.0,"expected_better_price":99.0}]}
    result=_apply_wait_better_entry_contract(payload)
    decision=result["decisions"][0]
    assert decision["expected_better_price"]==99.0
    assert decision["expected_wait_minutes"]==30
    assert decision["better_entry_reason"]=="atr_pullback"


def _frame(future_low: float) -> pd.DataFrame:
    rows=[]
    for minute in range(15):
        rows.append((f"2026-08-14 09:{30+minute:02d}",100.0,100.3,99.8,100.1,1000))
    rows.append(("2026-08-14 09:45",100.1,125.0,future_low,110.0,999999))
    index=pd.DatetimeIndex([pd.Timestamp(row[0],tz="America/New_York") for row in rows])
    return pd.DataFrame({"Open":[r[1] for r in rows],"High":[r[2] for r in rows],"Low":[r[3] for r in rows],"Close":[r[4] for r in rows],"Volume":[r[5] for r in rows]},index=index)


def _signal_snapshot(frame: pd.DataFrame) -> dict:
    return reconstruct_snapshot_from_frame(
        symbol="TEST",
        decision={
            "source_last_bar_time":"2026-08-14T09:44:00-04:00",
            "current_price":100.1,
        },
        frame=frame,
    )


def test_expected_better_price_is_invariant_to_post_signal_future_bars() -> None:
    # Both histories are identical through signal_time. Only the forbidden future
    # 09:45 bar differs. Causal snapshot reconstruction must remove that difference
    # before the V7 timing model generates expected_better_price.
    cheap_future=_signal_snapshot(_frame(70.0))
    expensive_future=_signal_snapshot(_frame(105.0))
    assert cheap_future==expensive_future

    def assess(snapshot: dict):
        return IntradayTimingModel().assess(
            base_status="BUY_NOW",
            current_price=float(snapshot["current_price"]),
            entry_low=99.0,
            entry_high=100.0,
            stop_loss=96.0,
            session_low=float(snapshot["session_low"]),
            session_high=float(snapshot["session_high"]),
            session_vwap=99.95,
            last_5m_return_pct=-0.25,
            intraday_volatility_pct=1.2,
            minutes_since_open=14,
            probability_up_1d=.54,
            probability_up_5d=.61,
        )

    left=assess(cheap_future)
    right=assess(expensive_future)
    assert left.action=="WAIT_BETTER_ENTRY"
    assert left.expected_better_price==right.expected_better_price


def test_extended_intraday_explicitly_ignores_bars_after_signal_time(monkeypatch) -> None:
    snapshot=LiveSnapshot(
        symbol="TEST",
        current_price=100.1,
        session_open=100.0,
        session_high=100.3,
        session_low=99.8,
        opening_15m_high=100.3,
        opening_15m_low=99.8,
        return_from_open_pct=0.1,
        opening_15m_volume=15000,
        recent_opening_volume_median=14000,
        volume_ratio=1.07,
        bar_count=15,
        last_bar_time="2026-08-14T09:44:00-04:00",
    )
    evaluated_at=datetime(2026,8,14,9,45,tzinfo=NY)

    class FakeTicker:
        def __init__(self, frame): self.frame=frame
        def history(self, **kwargs): return self.frame.copy()

    def features(frame):
        monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda symbol:FakeTicker(frame)))
        return timing_module._extended_intraday("TEST",evaluated_at,snapshot)

    cheap=features(_frame(70.0))
    expensive=features(_frame(105.0))
    assert cheap==expensive
    assert cheap["minutes_since_open"]==14
