from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from src.v6_daily.lab_replay import replay_stock_db_accuracy_lab
from src.v6_daily.research_backfill import backfill_accuracy_research_history


def _create_source_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE stock_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                date DATE NOT NULL,
                open FLOAT,
                high FLOAT,
                low FLOAT,
                close FLOAT,
                volume FLOAT,
                amount FLOAT,
                pct_chg FLOAT,
                ma5 FLOAT,
                ma10 FLOAT,
                ma20 FLOAT,
                volume_ratio FLOAT,
                data_source VARCHAR(50),
                UNIQUE(code, date)
            )
            """
        )
        for code in ("MSFT", "GOOGL"):
            for offset, trade_date in enumerate(pd.date_range("2025-06-02", periods=5, freq="B")):
                price = 100.0 + offset
                conn.execute(
                    """
                    INSERT INTO stock_daily(code,date,open,high,low,close,volume,data_source)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        code,
                        trade_date.date().isoformat(),
                        price - 0.2,
                        price + 0.8,
                        price - 0.8,
                        price,
                        1_000_000 + offset,
                        "production-test",
                    ),
                )
        conn.commit()


def _history_frame(code: str, periods: int = 140) -> pd.DataFrame:
    code_bias = {
        "MSFT": 0.35,
        "GOOGL": 0.28,
        "SPY": 0.18,
        "QQQ": 0.24,
    }.get(code, 0.2)
    rows = []
    for index, trade_date in enumerate(pd.date_range("2025-01-02", periods=periods, freq="B")):
        close = 100.0 + index * code_bias + ((index % 9) - 4) * 0.08
        rows.append(
            {
                "code": code,
                "date": trade_date,
                "open": close - 0.25,
                "high": close + 0.75,
                "low": close - 0.75,
                "close": close,
                "volume": 1_000_000 + index * 500,
                "amount": close * (1_000_000 + index * 500),
                "pct_chg": 0.0,
            }
        )
    return pd.DataFrame(rows)


class _FakeFetcher:
    def __init__(self, failing_codes: set[str] | None = None):
        self.failing_codes = failing_codes or set()

    def get_daily_data(self, code: str, *, start_date: str, end_date: str):
        assert start_date < end_date
        if code in self.failing_codes:
            raise RuntimeError(f"synthetic fetch failure for {code}")
        return _history_frame(code)


def test_research_backfill_isolated_and_produces_replay_observations(tmp_path: Path) -> None:
    source = tmp_path / "production.db"
    research = tmp_path / "research.db"
    _create_source_db(source)

    with sqlite3.connect(source) as conn:
        source_rows_before = int(conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0])

    summary = backfill_accuracy_research_history(
        source,
        research,
        codes=["MSFT", "GOOGL"],
        history_years=3,
        minimum_bars=81,
        fetcher=_FakeFetcher(),
        as_of=date(2025, 8, 1),
    )

    assert summary["status"] == "ok"
    assert summary["source_read_only"] is True
    assert summary["source_unchanged"] is True
    assert summary["eligible_targets"] == ["MSFT", "GOOGL"]
    assert summary["benchmark_ready"] == {"SPY": True, "QQQ": True}
    assert summary["research_rows"] > source_rows_before

    with sqlite3.connect(source) as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]) == source_rows_before
        assert int(conn.execute("SELECT COUNT(*) FROM stock_daily WHERE code='SPY'").fetchone()[0]) == 0
        assert int(conn.execute("SELECT COUNT(*) FROM stock_daily WHERE code='QQQ'").fetchone()[0]) == 0

    replay = replay_stock_db_accuracy_lab(
        str(research),
        codes=["MSFT"],
        min_samples=3,
        promotion_min_samples=6,
    )
    assert replay["observations"] > 0
    assert replay["auto_promotion"] is False
    assert replay["auto_weight_tuning"] is False
    champion = [item for item in replay["results"] if item["variant"] == "champion"]
    assert {int(item["horizon_days"]) for item in champion} == {5, 10, 20}
    assert all(int(item["non_overlapping"]["samples"]) > 0 for item in champion)
    assert all(item["non_overlapping"]["hit_rate_ci95_low_pct"] is not None for item in champion)
    assert all(item["yearly_walk_forward"] for item in champion)


def test_research_backfill_keeps_usable_targets_when_one_symbol_fails(tmp_path: Path) -> None:
    source = tmp_path / "production.db"
    research = tmp_path / "research.db"
    _create_source_db(source)

    summary = backfill_accuracy_research_history(
        source,
        research,
        codes=["MSFT", "GOOGL"],
        history_years=3,
        minimum_bars=81,
        fetcher=_FakeFetcher({"GOOGL"}),
        as_of=date(2025, 8, 1),
    )

    assert summary["status"] == "partial"
    assert summary["source_unchanged"] is True
    assert summary["eligible_targets"] == ["MSFT"]
    assert summary["ineligible_targets"] == ["GOOGL"]
    assert summary["benchmark_ready"] == {"SPY": True, "QQQ": True}
    assert summary["errors"] == [
        {"code": "GOOGL", "error": "RuntimeError: synthetic fetch failure for GOOGL"}
    ]
