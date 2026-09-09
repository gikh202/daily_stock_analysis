from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

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

    assert history._row_probability(row, "probability_up") == pytest.approx(0.60)
    assert history._row_probability(
        row, "challenger_probability_up"
    ) == pytest.approx(0.90)


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
    assert metrics["champion_brier_score"] == pytest.approx(0.16)
    assert metrics["challenger_brier_score"] == pytest.approx(0.01)
    assert metrics["majority_baseline_accuracy"] == pytest.approx(1.0)
    assert metrics["challenger_direction_accuracy"] == pytest.approx(1.0)
    assert metrics["challenger_direction_skill"] == pytest.approx(0.0)
    assert metrics["challenger_bullish_samples"] == 10
    assert metrics["challenger_bullish_mean_alpha_pct"] == pytest.approx(0.5)

    selection = history.select_champion(
        as_of_date="2026-03-01",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=5,
        min_brier_improvement=0.01,
    )
    assert selection["champion_model"] == "calibrated_ensemble"
    assert selection["status"] == "observing"
    assert "beats_majority_baseline" in selection["blocked_reasons"]
    assert selection["reverse_signal_shadow"]["production_enabled"] is False



def _build_balanced_skill_history(path: Path, *, inverse_challenger: bool = False) -> None:
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
    for idx in range(1, 21):
        up = idx % 2 == 0
        champion_p = 0.55
        correct_challenger = 0.75 if up else 0.25
        challenger_p = 1.0 - correct_challenger if inverse_challenger else correct_challenger
        ret = 1.0 if up else -1.0
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",
            (idx, "v8-test", "risk_on", f"2026-01-{idx:02d}"),
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
            (idx, 5, f"2026-02-{idx:02d}", ret, max(ret, 1.5), min(ret, -0.5), ret),
        )
    conn.commit()
    conn.close()


def test_challenger_promotes_only_when_it_beats_champion_and_real_baselines(tmp_path: Path) -> None:
    path = tmp_path / "skill.db"
    _build_balanced_skill_history(path)
    history = ForecastHistory(str(path))

    metrics = history.paired_model_metrics(
        as_of_date="2026-03-15",
        horizon_days=5,
        regime="risk_on",
    )
    assert metrics["positive_rate"] == pytest.approx(0.5)
    assert metrics["majority_baseline_accuracy"] == pytest.approx(0.5)
    assert metrics["champion_direction_accuracy"] == pytest.approx(0.5)
    assert metrics["challenger_direction_accuracy"] == pytest.approx(1.0)
    assert metrics["challenger_direction_skill"] == pytest.approx(0.5)
    assert metrics["challenger_brier_score"] < metrics["base_rate_brier_score"]
    assert metrics["challenger_log_loss"] < metrics["base_rate_log_loss"]
    assert metrics["challenger_bullish_samples"] == 10
    assert metrics["challenger_bullish_positive_alpha_rate"] == pytest.approx(1.0)
    assert metrics["challenger_bullish_mean_alpha_pct"] == pytest.approx(1.0)

    selection = history.select_champion(
        as_of_date="2026-03-15",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=10,
        min_bullish_alpha_samples=5,
    )
    assert selection["status"] == "promoted"
    assert selection["champion_model"] == "momentum_challenger"
    assert all(selection["promotion_gates"].values())


def test_inverse_signal_is_shadow_warning_never_auto_promoted(tmp_path: Path) -> None:
    path = tmp_path / "inverse.db"
    _build_balanced_skill_history(path, inverse_challenger=True)
    history = ForecastHistory(str(path))

    selection = history.select_champion(
        as_of_date="2026-03-15",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=10,
    )
    assert selection["status"] == "observing"
    assert selection["champion_model"] == "calibrated_ensemble"
    assert selection["reverse_signal_shadow"]["production_enabled"] is False
    assert selection["reverse_signal_shadow"]["warning"] is True
    assert selection["reverse_signal_shadow"]["challenger_inverse_direction_skill"] > 0
