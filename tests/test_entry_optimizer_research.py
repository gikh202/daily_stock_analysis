from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.us_open_research_ledger import (
    compute_outcome,
    connect,
    record_intraday_bars,
)

NY = ZoneInfo("America/New_York")


def _frame():
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-08-14 09:44", tz=NY),
            pd.Timestamp("2026-08-14 09:45", tz=NY),
            pd.Timestamp("2026-08-14 09:50", tz=NY),
            pd.Timestamp("2026-08-14 16:00", tz=NY),
        ]
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 100.0, 99.8, 101.0],
            "High": [100.1, 100.2, 100.0, 101.2],
            "Low": [99.9, 99.7, 99.3, 100.8],
            "Close": [100.0, 99.8, 99.6, 101.0],
            "Volume": [1000, 1200, 1300, 1500],
        },
        index=index,
    )


def test_intraday_store_persists_1m_and_5m(tmp_path):
    db = tmp_path / "research.db"
    counts = record_intraday_bars(db, symbol="TEST", frame=_frame())
    assert counts["1m"] == 4
    assert counts["5m"] >= 2
    with connect(db) as conn:
        one = conn.execute(
            "SELECT COUNT(*) FROM us_intraday_bars WHERE interval='1m'"
        ).fetchone()[0]
        five = conn.execute(
            "SELECT COUNT(*) FROM us_intraday_bars WHERE interval='5m'"
        ).fetchone()[0]
    assert one == 4
    assert five >= 2


def test_outcome_compares_optimized_entry_with_immediate():
    decision = {
        "ideal_entry_price": 99.5,
        "expected_wait_minutes": 15,
        "expected_better_price": 99.5,
    }
    row = {
        "signal_bar_time": "2026-08-14T09:44:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps(
            {"execution": {"stop_loss": 97.0, "targets": [104.0]}}
        ),
        "decision_json": json.dumps(decision),
    }
    outcome = compute_outcome(row, _frame())
    assert outcome is not None
    assert outcome["ideal_entry_hit"] is True
    assert outcome["minutes_to_ideal_entry"] == pytest.approx(6.0)
    assert outcome["ideal_entry_policy_return_pct"] > outcome["close_return_pct"]
    assert outcome["ideal_entry_alpha_vs_immediate_pct"] > 0
