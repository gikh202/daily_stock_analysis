from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from src.forecasting.history import ForecastHistory


def _create_history_db(path: Path, samples: int) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE v6_forecast_runs (
            id INTEGER PRIMARY KEY,
            engine_version TEXT,
            market_regime TEXT,
            effective_trade_date TEXT,
            symbol TEXT,
            instrument_type TEXT
        );
        CREATE TABLE v6_horizon_forecasts (
            id INTEGER PRIMARY KEY,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            score REAL,
            payload_json TEXT
        );
        CREATE TABLE v6_forecast_outcomes (
            id INTEGER PRIMARY KEY,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            end_trade_date TEXT,
            return_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            excess_vs_spy_pct REAL
        );
        """
    )
    start = date(2025, 1, 2)
    for index in range(samples):
        run_id = index + 1
        effective = start + timedelta(days=index)
        end = effective + timedelta(days=5)
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?,?,?)",
            (run_id, "v7.3-test", "risk_on", effective.isoformat(), "MSFT", "STOCK"),
        )
        conn.execute(
            "INSERT INTO v6_horizon_forecasts(forecast_run_id,horizon_days,score,payload_json) VALUES (?,?,?,?)",
            (
                run_id,
                5,
                60.0,
                json.dumps(
                    {
                        "probability_up": 0.60,
                        "challenger_probability_up": 0.55,
                        "champion_model": "calibrated_ensemble",
                        "challenger_model": "momentum_challenger",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,end_trade_date,return_pct,mfe_pct,mae_pct,excess_vs_spy_pct) VALUES (?,?,?,?,?,?,?)",
            (run_id, 5, end.isoformat(), 1.0, 1.5, -0.5, 0.2),
        )
    conn.commit()
    conn.close()


def _profile(path: Path):
    history = ForecastHistory(str(path))
    return history, history.calibration(
        as_of_date="2026-12-31",
        horizon_days=5,
        raw_probability_up=0.60,
        regime="risk_on",
        symbol="MSFT",
        instrument_type="STOCK",
    )


def test_default_forecast_maturity_threshold_is_50(tmp_path: Path) -> None:
    path = tmp_path / "history_49.db"
    _create_history_db(path, 49)
    history, profile = _profile(path)
    assert history.minimum_samples == 50
    assert profile.samples == 49
    assert profile.status == "shrunk"


def test_forecast_becomes_mature_at_50_samples(tmp_path: Path) -> None:
    path = tmp_path / "history_50.db"
    _create_history_db(path, 50)
    history, profile = _profile(path)
    assert history.minimum_samples == 50
    assert profile.samples == 50
    assert profile.status == "mature"
