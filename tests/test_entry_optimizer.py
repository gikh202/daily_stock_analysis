from src.forecasting.entry_optimizer import EntryOptimizer


def test_optimizer_prefers_risk_bounded_pullback_when_current_is_extended():
    result = EntryOptimizer().optimize(
        current_price=105.0,
        stop_loss=98.0,
        targets=(112.0, 118.0),
        entry_low=101.0,
        entry_high=103.0,
        session_low=101.5,
        session_high=105.2,
        session_vwap=102.8,
        ema20=102.9,
        opening_range_low=102.2,
        previous_close=102.5,
        intraday_volatility_pct=1.2,
        last_5m_return_pct=-0.2,
        probability_up_1d=0.58,
        probability_up_5d=0.64,
        expected_return_5d_pct=2.0,
        market_regime="risk_on",
    )
    assert result.ideal_entry_price is not None
    assert result.ideal_entry_price < 105.0
    assert result.acceptable_entry_low <= result.ideal_entry_price <= result.acceptable_entry_high
    assert result.no_chase_above > result.acceptable_entry_high
    assert len(result.candidates) >= 3


def test_optimizer_never_uses_price_below_hard_stop():
    result = EntryOptimizer().optimize(
        current_price=100.0,
        stop_loss=97.0,
        targets=(106.0,),
        session_low=96.0,
        session_high=101.0,
        session_vwap=99.0,
        opening_range_low=96.5,
        intraday_volatility_pct=1.0,
    )
    assert result.ideal_entry_price is not None
    assert result.ideal_entry_price > 97.0
    assert all(item.price > 97.0 for item in result.candidates)


def test_optimizer_refuses_to_invent_plan_without_valid_stop():
    result = EntryOptimizer().optimize(current_price=100.0, stop_loss=None)
    assert result.ideal_entry_price is None
    assert result.candidates == ()


def test_wait_mode_excludes_current_price_candidate():
    result = EntryOptimizer().optimize(
        current_price=100.0,
        stop_loss=95.0,
        targets=(106.0,),
        entry_low=98.0,
        entry_high=100.0,
        session_low=97.8,
        session_high=100.2,
        session_vwap=99.0,
        ema20=98.8,
        opening_range_low=98.2,
        previous_close=98.5,
        intraday_volatility_pct=1.0,
        probability_up_1d=0.55,
        probability_up_5d=0.60,
        allow_current=False,
    )
    assert result.ideal_entry_price is not None
    assert result.ideal_entry_price < 99.95
    assert all(item.source != "current" for item in result.candidates)
