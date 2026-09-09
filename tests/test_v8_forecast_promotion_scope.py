from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.forecasting.history import ForecastHistory


def _build_history(
    path: Path,
    *,
    row_symbol: str = "TEST",
    challenger_mode: str = "strong",
) -> None:
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            score REAL,
            payload_json TEXT
        );
        CREATE TABLE v6_forecast_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    for idx in range(1, 241):
        positive = idx % 2 == 0
        champion_p = 0.60
        if challenger_mode == "strong":
            challenger_p = 0.70 if positive else 0.30
        elif challenger_mode == "weak":
            challenger_p = 0.55 if positive else 0.45
        elif challenger_mode == "inverse":
            challenger_p = 0.30 if positive else 0.70
        else:
            raise ValueError(challenger_mode)

        realized = 1.0 if positive else -1.0
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?,?,?)",
            (
                idx,
                "v8-test",
                "risk_on",
                f"2025-{1 + ((idx - 1) // 28):02d}-{1 + ((idx - 1) % 28):02d}",
                row_symbol,
                "STOCK",
            ),
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
                champion_p * 100.0,
                json.dumps(
                    {
                        "probability_up": champion_p,
                        "challenger_probability_up": challenger_p,
                        "champion_model": "calibrated_ensemble",
                        "challenger_model": "momentum_challenger",
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
            (
                idx,
                5,
                f"2026-{1 + ((idx - 1) // 28):02d}-{1 + ((idx - 1) % 28):02d}",
                realized,
                max(1.5, realized),
                min(-0.5, realized),
                realized,
            ),
        )
    conn.commit()
    conn.close()


def test_symbol_level_strong_challenger_can_pass_all_promotion_gates(
    tmp_path: Path,
) -> None:
    db = tmp_path / "symbol.db"
    _build_history(db)
    selection = ForecastHistory(str(db)).select_champion(
        as_of_date="2027-01-01",
        horizon_days=5,
        regime="risk_on",
        symbol="TEST",
        instrument_type="STOCK",
    )

    assert selection["evaluation_scope"] in {"symbol", "symbol_regime"}
    assert selection["status"] == "promoted"
    assert selection["champion_model"] == "momentum_challenger"
    assert selection["challenger_metrics"]["signal_samples"] == 240
    assert selection["challenger_metrics"]["signal_accuracy"] == 1.0
    assert selection["promotion_gates"]["symbol_specific_scope"] is True
    assert selection["promotion_gates"]["signal_sample_floor"] is True
    assert selection["promotion_gates"]["signal_accuracy_floor"] is True
    assert selection["promotion_gates"]["beats_inverse_signal"] is True
    assert selection["promotion_failures"] == []


def test_pooled_fallback_cannot_promote_a_specific_symbol(tmp_path: Path) -> None:
    db = tmp_path / "pooled.db"
    _build_history(db, row_symbol="OTHER")
    selection = ForecastHistory(str(db)).select_champion(
        as_of_date="2027-01-01",
        horizon_days=5,
        regime="risk_on",
        symbol="TARGET",
        instrument_type="STOCK",
    )

    assert selection["evaluation_scope"] in {
        "instrument_type",
        "instrument_regime",
        "regime",
        "global",
    }
    assert selection["status"] == "observing"
    assert selection["champion_model"] == "calibrated_ensemble"
    assert selection["promotion_gates"]["symbol_specific_scope"] is False
    assert "symbol_specific_scope" in selection["promotion_failures"]


def test_correct_but_weak_probabilities_cannot_pass_strong_signal_gate(
    tmp_path: Path,
) -> None:
    db = tmp_path / "weak.db"
    _build_history(db, challenger_mode="weak")
    selection = ForecastHistory(str(db)).select_champion(
        as_of_date="2027-01-01",
        horizon_days=5,
        regime="risk_on",
        symbol="TEST",
        instrument_type="STOCK",
    )

    assert selection["challenger_metrics"]["direction_accuracy"] == 1.0
    assert selection["challenger_metrics"]["signal_samples"] == 0
    assert selection["status"] == "observing"
    assert selection["promotion_gates"]["signal_sample_floor"] is False
    assert selection["promotion_gates"]["signal_accuracy_floor"] is False


def test_inverse_challenger_is_blocked_even_with_large_confidence(
    tmp_path: Path,
) -> None:
    db = tmp_path / "inverse.db"
    _build_history(db, challenger_mode="inverse")
    selection = ForecastHistory(str(db)).select_champion(
        as_of_date="2027-01-01",
        horizon_days=5,
        regime="risk_on",
        symbol="TEST",
        instrument_type="STOCK",
    )

    assert selection["status"] == "observing"
    assert selection["promotion_gates"]["beats_inverse_signal"] is False
    assert (
        selection["reverse_signal_shadow"]["challenger_inverse_direction_accuracy"]
        > selection["challenger_metrics"]["direction_accuracy"]
    )
