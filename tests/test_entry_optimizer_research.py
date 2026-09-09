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


def test_v3_ledger_migrates_to_v4_before_regime_index(tmp_path):
    db = tmp_path / "legacy.db"
    import sqlite3

    legacy = sqlite3.connect(db)
    legacy.execute(
        """
        CREATE TABLE us_open_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version TEXT NOT NULL,
            signal_key TEXT NOT NULL UNIQUE,
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            source_run_id TEXT,
            source_trade_date TEXT,
            evaluated_at TEXT NOT NULL,
            signal_bar_time TEXT NOT NULL,
            signal_price REAL NOT NULL,
            decision_status TEXT NOT NULL,
            packet_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            settled_at TEXT,
            close_return_pct REAL,
            return_60m_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            stop_hit INTEGER,
            target1_hit INTEGER,
            first_touch TEXT,
            modeled_exit_return_pct REAL,
            better_entry_hit INTEGER,
            best_future_improvement_pct REAL,
            minutes_to_reference_better_price REAL,
            close_plan_json TEXT,
            execution_transition TEXT,
            outcome_json TEXT
        )
        """
    )
    legacy.commit()
    legacy.close()

    with connect(db) as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(us_open_signals)")
        }
        indexes = {
            str(row["name"])
            for row in conn.execute("PRAGMA index_list(us_open_signals)")
        }

    assert "market_regime" in columns
    assert "ideal_entry_hit" in columns
    assert "ideal_entry_alpha_vs_immediate_pct" in columns
    assert "ix_us_open_signals_regime" in indexes
