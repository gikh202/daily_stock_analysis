from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.forecasting.history import ForecastHistory


def _build_promoted_history(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE v6_forecast_runs (
            id INTEGER PRIMARY KEY,
            engine_version TEXT,
            market_regime TEXT,
            effective_trade_date TEXT
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
    for idx in range(1, 11):
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",
            (idx, "v7.1-test", "risk_on", "2026-01-10"),
        )
        conn.execute(
            """
            INSERT INTO v6_horizon_forecasts(
                forecast_run_id,horizon_days,score,payload_json
            ) VALUES (?,?,?,?)
            """,
            (
                idx,
                5,
                90.0,
                json.dumps(
                    {
                        "probability_up": 0.90,
                        "challenger_probability_up": 0.60,
                        "champion_model": "momentum_challenger",
                        "challenger_model": "calibrated_ensemble",
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO v6_forecast_outcomes(
                forecast_run_id,horizon_days,end_trade_date,return_pct,
                mfe_pct,mae_pct,excess_vs_spy_pct
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (idx, 5, "2026-02-10", 1.0, 2.0, -0.5, 0.5),
        )
    conn.commit()
    conn.close()


def test_promoted_rows_keep_stable_model_probability_identity(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _build_promoted_history(path)
    history = ForecastHistory(str(path))
    row = history._rows(as_of_date="2026-03-01", horizon_days=5)[0]

    assert history._row_probability(row, "probability_up") == 0.60
    assert history._row_probability(row, "challenger_probability_up") == 0.90


def test_paired_promotion_metrics_do_not_reverse_after_promotion(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _build_promoted_history(path)
    history = ForecastHistory(str(path))

    metrics = history.paired_model_metrics(
        as_of_date="2026-03-01",
        horizon_days=5,
        regime="risk_on",
    )
    assert metrics["samples"] == 10
    assert metrics["champion_brier_score"] == 0.16
    assert metrics["challenger_brier_score"] == 0.01

    selection = history.select_champion(
        as_of_date="2026-03-01",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=5,
        min_brier_improvement=0.01,
    )
    assert selection["champion_model"] == "momentum_challenger"
    assert selection["status"] == "promoted"
