from __future__ import annotations

import json

import pandas as pd

from scripts.us_open_research_ledger import compute_outcome


def _frame(future_low: float) -> pd.DataFrame:
    rows = [
        ("2026-08-14 09:44", 100.0, 100.2, 99.8, 100.0, 1000),
        ("2026-08-14 09:50", 100.0, 101.0, future_low, 100.5, 1000),
        ("2026-08-14 16:00", 101.0, 101.2, 100.8, 101.0, 1000),
    ]
    idx = pd.DatetimeIndex([pd.Timestamp(x[0], tz="America/New_York") for x in rows])
    return pd.DataFrame(
        {
            "Open": [x[1] for x in rows],
            "High": [x[2] for x in rows],
            "Low": [x[3] for x in rows],
            "Close": [x[4] for x in rows],
            "Volume": [x[5] for x in rows],
        },
        index=idx,
    )


def _row():
    return {
        "signal_bar_time": "2026-08-14T09:44:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps({"execution": {"stop_loss": 95, "targets": [110]}}),
        "decision_json": json.dumps(
            {
                "action": "WAIT_BETTER_ENTRY",
                "expected_better_price": 99.0,
                "expected_wait_minutes": 30,
                "better_entry_reason": "atr_pullback",
            }
        ),
    }


def test_wait_expected_price_hit():
    result = compute_outcome(_row(), _frame(98.0))
    assert result["better_entry_hit"] is True
    assert result["actual_entry_price"] == 99.0
    assert result["price_improvement_pct"] == 1.0


def test_wait_expected_price_miss():
    result = compute_outcome(_row(), _frame(100.5))
    assert result["better_entry_hit"] is False
    assert result["actual_entry_price"] is None


def test_late_session_low_does_not_count_as_wait_hit():
    rows = [
        ("2026-08-14 09:44", 100.0, 100.2, 99.8, 100.0, 1000),
        ("2026-08-14 10:10", 100.0, 100.8, 100.2, 100.5, 1000),
        ("2026-08-14 10:15", 100.5, 100.7, 98.0, 98.5, 1000),
        ("2026-08-14 16:00", 101.0, 101.2, 100.8, 101.0, 1000),
    ]
    idx = pd.DatetimeIndex([pd.Timestamp(x[0], tz="America/New_York") for x in rows])
    frame = pd.DataFrame(
        {
            "Open": [x[1] for x in rows],
            "High": [x[2] for x in rows],
            "Low": [x[3] for x in rows],
            "Close": [x[4] for x in rows],
            "Volume": [x[5] for x in rows],
        },
        index=idx,
    )
    result = compute_outcome(_row(), frame)
    assert result["better_entry_hit"] is False
    assert result["minutes_to_reference_better_price"] is None
