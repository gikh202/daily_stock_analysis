from __future__ import annotations

import json

import pytest

from scripts.backtest_entry_optimizer import _simulate_after_fill, _simulate_row


def test_same_bar_stop_and_target_is_conservatively_a_stop() -> None:
    bars = [
        {"low": 97.0, "high": 103.0, "close": 101.0},
        {"low": 100.0, "high": 104.0, "close": 103.0},
    ]
    result = _simulate_after_fill(
        bars,
        fill_index=0,
        raw_entry_price=100.0,
        stop=98.0,
        target=102.0,
        slippage_bps=0.0,
        fee_bps=0.0,
    )
    assert result["ambiguous"] is True
    assert result["first_touch"] == "ambiguous_stop_target_same_bar_conservative_stop"
    assert result["return_pct"] == pytest.approx(-2.0)


def test_unfilled_limit_stays_cash_and_records_missed_winner_cost() -> None:
    row = {
        "session_date": "2026-09-09",
        "symbol": "TEST",
        "market_regime": "risk_on",
        "signal_bar_time": "2026-09-09T09:30:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps(
            {"execution": {"stop_loss": 96.0, "targets": [103.0]}}
        ),
        "decision_json": json.dumps(
            {"ideal_entry_price": 95.0, "expected_wait_minutes": 5}
        ),
    }
    bars = [
        {
            "bar_time": "2026-09-09T09:31:00-04:00",
            "low": 99.0,
            "high": 101.0,
            "close": 100.5,
        },
        {
            "bar_time": "2026-09-09T09:32:00-04:00",
            "low": 100.0,
            "high": 104.0,
            "close": 103.5,
        },
    ]
    result = _simulate_row(row, bars, fee_bps=0.0, slippage_bps=0.0)
    assert result is not None
    assert result["filled"] is False
    assert result["optimized_return_pct"] == 0.0
    assert result["immediate_first_touch"] == "target1"
    assert result["immediate_return_pct"] == pytest.approx(3.0)
    assert result["no_fill_opportunity_cost_pct"] == pytest.approx(3.0)
    assert result["alpha_vs_immediate_pct"] == pytest.approx(-3.0)


def test_round_trip_cost_and_slippage_reduce_modeled_return() -> None:
    bars = [
        {"low": 100.0, "high": 103.0, "close": 102.0},
    ]
    free = _simulate_after_fill(
        bars,
        fill_index=0,
        raw_entry_price=100.0,
        stop=95.0,
        target=102.0,
        slippage_bps=0.0,
        fee_bps=0.0,
    )
    costly = _simulate_after_fill(
        bars,
        fill_index=0,
        raw_entry_price=100.0,
        stop=95.0,
        target=102.0,
        slippage_bps=5.0,
        fee_bps=5.0,
    )
    assert costly["return_pct"] < free["return_pct"]
