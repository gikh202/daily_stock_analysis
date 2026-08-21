from __future__ import annotations

import inspect

from scripts.run_us_open_timing import OpenTimingDecision, _decision_payload
from src.forecasting.timing import IntradayTimingModel


def test_wait_decision_payload_exposes_reference_price_window_and_reason():
    decision = OpenTimingDecision(
        symbol="TEST", action="WAIT_BETTER_ENTRY", label="wait", reason="test",
        current_price=100.0, entry_low=98.0, entry_high=101.0, stop_loss=95.0,
        targets=(105.0,), starter_position_pct=0.0, max_position_pct=20.0,
        return_from_open_pct=0.5, volume_ratio=1.0, probability_up_1d=0.55,
        probability_up_5d=0.60, probability_up_20d=0.58,
        expected_return_5d_pct=1.0, expected_alpha_5d_pct=0.2,
        forecast_confidence=0.7, better_entry_score=0.7,
        better_entry_probability=0.7, expected_better_price=99.4,
        expected_improvement_pct=0.6, recheck_minutes=30, terminal=False,
        source_trade_date="2026-08-20", source_last_bar_time="2026-08-21T09:44:00-04:00",
    )
    payload = _decision_payload(decision)
    assert payload["expected_better_price"] == 99.4
    assert payload["expected_wait_minutes"] == 30
    assert payload["better_entry_reason"] == "intraday_volatility_pullback"


def test_intraday_timing_model_has_no_future_market_input_contract():
    forbidden = {"future", "future_low", "future_high", "close_price", "session_close"}
    params = set(inspect.signature(IntradayTimingModel.assess).parameters)
    assert forbidden.isdisjoint(params)
    decision = IntradayTimingModel().assess(
        base_status="BUY_NOW", current_price=105.0, entry_low=100.0,
        entry_high=105.0, stop_loss=96.0, session_low=101.0,
        session_high=105.2, session_vwap=103.5, last_5m_return_pct=-0.25,
        intraday_volatility_pct=1.2, minutes_since_open=25,
        probability_up_1d=0.54, probability_up_5d=0.61,
    )
    assert decision.action == "WAIT_BETTER_ENTRY"
    assert 96.0 < decision.expected_better_price < 105.0
