from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.forecasting.history import ForecastHistory


def _db(path: Path) -> None:
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
    for idx in range(1, 41):
        positive = idx <= 30
        champion_p = 0.75 if positive else 0.25
        challenger_p = 0.65 if positive else 0.35
        ret = 2.0 if positive else -1.5
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",
            (idx, "v7-test", "risk_on", f"2026-01-{(idx % 20) + 1:02d}"),
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
                champion_p * 100,
                json.dumps(
                    {
                        "probability_up": champion_p,
                        "challenger_probability_up": challenger_p,
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
                f"2026-02-{(idx % 20) + 1:02d}",
                ret,
                max(ret, 3.0),
                min(ret, -1.0),
                ret - 0.5,
            ),
        )
    conn.commit()
    conn.close()


def test_calibration_uses_only_strictly_prior_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(
        str(path),
        minimum_samples=20,
        minimum_regime_samples=10,
    )
    profile = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.75,
        regime="risk_on",
    )
    assert profile.samples >= 20
    assert profile.status == "mature"
    assert 0.5 < profile.probability_up < 0.95
    assert profile.brier_score is not None
    assert profile.log_loss is not None
    assert profile.ece is not None

    no_future = history.calibration(
        as_of_date="2026-01-15",
        horizon_days=5,
        raw_probability_up=0.75,
        regime="risk_on",
    )
    assert no_future.samples == 0
    assert no_future.status == "prior_only"


def test_missing_or_invalid_as_of_date_never_unlocks_history(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(str(path))

    for as_of in (None, "", "not-a-date"):
        profile = history.calibration(
            as_of_date=as_of,
            horizon_days=5,
            raw_probability_up=0.61,
            regime="risk_on",
        )
        assert profile.samples == 0
        assert profile.status == "prior_only"
        assert profile.probability_up == 0.61
        assert profile.source == "missing_as_of"

        selection = history.select_champion(
            as_of_date=as_of,
            horizon_days=5,
            regime="risk_on",
        )
        assert selection["status"] == "cold_start"
        assert selection["champion_metrics"]["samples"] == 0
        assert selection["challenger_metrics"]["samples"] == 0


def test_challenger_calibration_uses_its_own_probability_series(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(
        str(path),
        minimum_samples=20,
        minimum_regime_samples=10,
    )

    champion = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.65,
        regime="risk_on",
        probability_key="probability_up",
    )
    challenger = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.65,
        regime="risk_on",
        probability_key="challenger_probability_up",
    )

    assert champion.source.endswith(":probability_up")
    assert challenger.source.endswith(":challenger_probability_up")
    assert challenger.samples == 30
    assert champion.samples == 40
    assert challenger.probability_up != champion.probability_up


def test_challenger_cannot_promote_without_forward_sample_floor(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(str(path))
    selection = history.select_champion(
        as_of_date="2026-03-01",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=200,
    )
    assert selection["champion_model"] == "calibrated_ensemble"
    assert selection["status"] in {"observing", "cold_start"}
