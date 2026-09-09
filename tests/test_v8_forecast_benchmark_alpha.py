from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from src.v6_daily.production_benchmarks import prepare_benchmark_settlement_db
from src.v6_daily.production_outcomes import backfill_missing_benchmark_outcomes


def _stock_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE stock_daily (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            data_source TEXT,
            UNIQUE(code, date)
        )
        """
    )
    conn.execute(
        "INSERT INTO stock_daily(code,date,open,high,low,close,volume) "
        "VALUES (?,?,?,?,?,?,?)",
        ("MSFT", "2026-01-02", 100.0, 101.0, 99.0, 100.0, 1000.0),
    )
    conn.commit()
    conn.close()


class _FakeFetcher:
    def get_daily_data(self, code: str, *, start_date: str, end_date: str):
        base = 100.0 if code == "SPY" else 200.0
        rows = []
        for index in range(6):
            value = base + index
            rows.append(
                {
                    "date": f"2026-01-{index + 2:02d}",
                    "open": value,
                    "high": value + 1.0,
                    "low": value - 1.0,
                    "close": value,
                    "volume": 1000.0 + index,
                }
            )
        return pd.DataFrame(rows)


def test_benchmark_settlement_clone_keeps_production_source_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stock_analysis.db"
    settlement = tmp_path / "stock_analysis_benchmark_settlement.db"
    _stock_db(source)

    with sqlite3.connect(source) as conn:
        before_rows = int(conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0])
        before_codes = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT code FROM stock_daily ORDER BY code"
            ).fetchall()
        }

    payload = prepare_benchmark_settlement_db(
        source,
        settlement,
        minimum_bars=3,
        history_days=30,
        fetcher=_FakeFetcher(),
        as_of=date(2026, 1, 10),
    )

    assert payload["ready"] is True
    assert payload["source_read_only"] is True
    assert payload["source_unchanged"] is True

    with sqlite3.connect(source) as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]) == before_rows
        assert {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT code FROM stock_daily ORDER BY code"
            ).fetchall()
        } == before_codes

    with sqlite3.connect(settlement) as conn:
        assert int(
            conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE code='SPY'"
            ).fetchone()[0]
        ) == 6
        assert int(
            conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE code='QQQ'"
            ).fetchone()[0]
        ) == 6


class _Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _forecast_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE v6_forecast_runs (
            id INTEGER PRIMARY KEY,
            effective_trade_date TEXT
        );
        CREATE TABLE v6_forecast_outcomes (
            id INTEGER PRIMARY KEY,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            return_pct REAL,
            benchmark_spy_return_pct REAL,
            benchmark_qqq_return_pct REAL,
            excess_vs_spy_pct REAL,
            excess_vs_qqq_pct REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO v6_forecast_runs(id,effective_trade_date) VALUES (?,?)",
        (1, "2026-01-02"),
    )
    conn.execute(
        """
        INSERT INTO v6_forecast_outcomes(
            id,forecast_run_id,horizon_days,return_pct,
            benchmark_spy_return_pct,benchmark_qqq_return_pct,
            excess_vs_spy_pct,excess_vs_qqq_pct
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (1, 1, 2, 5.0, None, None, None, None),
    )
    conn.commit()
    conn.close()


def _benchmark_db(path: Path, *, include_benchmarks: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE stock_daily (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL,
            UNIQUE(code,date)
        )
        """
    )
    if include_benchmarks:
        rows = [
            ("SPY", "2026-01-02", 100.0),
            ("SPY", "2026-01-05", 101.0),
            ("SPY", "2026-01-06", 102.0),
            ("QQQ", "2026-01-02", 200.0),
            ("QQQ", "2026-01-05", 202.0),
            ("QQQ", "2026-01-06", 204.0),
        ]
        conn.executemany(
            "INSERT INTO stock_daily(code,date,close) VALUES (?,?,?)",
            rows,
        )
    conn.commit()
    conn.close()


def test_historical_outcomes_are_repaired_with_benchmark_alpha(
    tmp_path: Path,
) -> None:
    forecast_db = tmp_path / "v6_daily.db"
    benchmark_db = tmp_path / "settlement.db"
    _forecast_db(forecast_db)
    _benchmark_db(benchmark_db)

    result = backfill_missing_benchmark_outcomes(
        _Store(forecast_db),
        str(benchmark_db),
    )

    assert result["candidates"] == 1
    assert result["repaired_rows"] == 1
    assert result["repaired_spy"] == 1
    assert result["repaired_qqq"] == 1
    assert result["still_missing_spy"] == 0
    assert result["still_missing_qqq"] == 0

    with sqlite3.connect(forecast_db) as conn:
        row = conn.execute(
            """
            SELECT benchmark_spy_return_pct, benchmark_qqq_return_pct,
                   excess_vs_spy_pct, excess_vs_qqq_pct
            FROM v6_forecast_outcomes WHERE id=1
            """
        ).fetchone()
    assert row is not None
    assert round(float(row[0]), 6) == 2.0
    assert round(float(row[1]), 6) == 2.0
    assert round(float(row[2]), 6) == 3.0
    assert round(float(row[3]), 6) == 3.0


def test_missing_benchmark_history_is_safe_and_keeps_alpha_null(
    tmp_path: Path,
) -> None:
    forecast_db = tmp_path / "v6_daily.db"
    benchmark_db = tmp_path / "empty_settlement.db"
    _forecast_db(forecast_db)
    _benchmark_db(benchmark_db, include_benchmarks=False)

    result = backfill_missing_benchmark_outcomes(
        _Store(forecast_db),
        str(benchmark_db),
    )

    assert result["candidates"] == 1
    assert result["repaired_rows"] == 0
    assert result["still_missing_spy"] == 1
    assert result["still_missing_qqq"] == 1
