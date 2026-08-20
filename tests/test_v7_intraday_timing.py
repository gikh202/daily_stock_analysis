from src.forecasting.timing import IntradayTimingModel


def test_timing_waits_when_pullback_probability_has_material_value() -> None:
    decision=IntradayTimingModel().assess(base_status="BUY_NOW",current_price=105.0,entry_low=100.0,entry_high=105.0,stop_loss=96.0,session_low=101.0,session_high=105.2,session_vwap=103.5,last_5m_return_pct=-.25,intraday_volatility_pct=1.2,minutes_since_open=25,probability_up_1d=.54,probability_up_5d=.61)
    assert decision.action=="WAIT_BETTER_ENTRY" and decision.better_entry_probability>=.62 and decision.expected_better_price<105.0 and decision.terminal is False


def test_strong_continuation_can_keep_buy_now() -> None:
    decision=IntradayTimingModel().assess(base_status="BUY_NOW",current_price=103.5,entry_low=101.0,entry_high=104.0,stop_loss=97.0,session_low=102.0,session_high=104.0,session_vwap=103.3,last_5m_return_pct=.45,intraday_volatility_pct=.5,minutes_since_open=35,probability_up_1d=.64,probability_up_5d=.68)
    assert decision.action=="BUY_NOW" and decision.terminal is True


def test_hard_blocker_cannot_be_resurrected_by_timing_model() -> None:
    decision=IntradayTimingModel().assess(base_status="INVALIDATED",current_price=95.0,entry_low=100.0,entry_high=105.0,stop_loss=96.0,session_low=94.0,session_high=103.0,session_vwap=99.0,last_5m_return_pct=1.0,intraday_volatility_pct=2.0,minutes_since_open=40,probability_up_1d=.8,probability_up_5d=.8)
    assert decision.action=="INVALIDATED" and decision.terminal is True
