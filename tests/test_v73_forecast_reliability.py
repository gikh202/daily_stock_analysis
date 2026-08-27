from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.forecasting.engine import (
    _decision_weight,
    _joint_expected_return,
    _native_return_target,
)
from src.forecasting.history import ForecastHistory
from src.forecasting.models import ForecastHorizon
from src.forecasting.timing import IntradayTimingModel


def test_horizon_return_targets_do_not_borrow_neighbor_horizons() -> None:
    context = {
        "prediction_context": {
            "horizons": {
                "5d": {"target_return_pct": 6.0},
                "20d": {"target_return_pct": 12.0},
            }
        }
    }
    assert _native_return_target(context, 1) is None
    assert _native_return_target(context, 5) == 6.0
    assert _native_return_target(context, 10) is None
    assert _native_return_target(context, 20) == 12.0


def test_probability_return_reconciliation_removes_bearish_positive_conflict() -> None:
    reconciled, implied, weight = _joint_expected_return(
        0.60,
        0.35,
        2.0,
        calibration_samples=50,
    )
    assert implied < 0
    assert weight >= 0.70
    assert reconciled < 0


def test_10d_is_quarantined_until_reliability_floor() -> None:
    assert _decision_weight(10, "mature", 49) == 0.0
    assert _decision_weight(10, "shrunk", 100) == 0.0
    assert _decision_weight(10, "mature", 50) > 0.0
    assert _decision_weight(20, "prior_only", 0) == 0.0


def _hierarchical_db(path: Path) -> None:
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
    row_id = 1
    for symbol, instrument, positive in (
        ("MSFT", "STOCK", True),
        ("GOOGL", "STOCK", False),
        ("VOO", "ETF", False),
    ):
        for index in range(16):
            ret = 1.0 if positive else -1.0
            conn.execute(
                "INSERT INTO v6_forecast_runs VALUES (?,?,?,?,?,?)",
                (
                    row_id,
                    "v7-test",
                    "risk_on",
                    f"2026-01-{(index % 20) + 1:02d}",
                    symbol,
                    instrument,
                ),
            )
            conn.execute(
                "INSERT INTO v6_horizon_forecasts(forecast_run_id,horizon_days,score,payload_json) VALUES (?,?,?,?)",
                (
                    row_id,
                    5,
                    70.0,
                    json.dumps(
                        {
                            "probability_up": 0.70,
                            "challenger_probability_up": 0.65,
                        }
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,end_trade_date,return_pct,mfe_pct,mae_pct,excess_vs_spy_pct) VALUES (?,?,?,?,?,?,?)",
                (
                    row_id,
                    5,
                    f"2026-02-{(index % 20) + 1:02d}",
                    ret,
                    max(ret, 1.5),
                    min(ret, -0.5),
                    ret - 0.2,
                ),
            )
            row_id += 1
    conn.commit()
    conn.close()


def test_calibration_prefers_symbol_history_before_cross_asset_history(tmp_path: Path) -> None:
    path = tmp_path / "hierarchical.db"
    _hierarchical_db(path)
    history = ForecastHistory(
        str(path),
        minimum_samples=30,
        minimum_regime_samples=15,
    )
    msft = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.70,
        regime="risk_on",
        symbol="MSFT",
        instrument_type="STOCK",
    )
    voo = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.70,
        regime="risk_on",
        symbol="VOO",
        instrument_type="ETF",
    )
    assert msft.calibration_scope.startswith("symbol")
    assert voo.calibration_scope.startswith("symbol")
    assert msft.historical_direction_hit_rate == 1.0
    assert voo.historical_direction_hit_rate == 0.0
    assert msft.probability_up > voo.probability_up


def test_prior_only_probability_is_serialized_as_uncalibrated_tendency() -> None:
    horizon = ForecastHorizon(
        horizon_days=20,
        raw_probability_up=0.77,
        probability_up=0.77,
        expected_return_pct=1.0,
        expected_alpha_vs_spy_pct=0.2,
        p10_return_pct=-2.0,
        p50_return_pct=1.0,
        p90_return_pct=4.0,
        expected_mfe_pct=3.0,
        expected_mae_pct=-2.0,
        evidence_coverage=0.8,
        forecast_confidence=0.75,
        calibration_samples=0,
        calibration_status="prior_only",
        regime="risk_on",
        champion_model="calibrated_ensemble",
        challenger_model="momentum_challenger",
        challenger_probability_up=0.70,
        direction="bullish",
        score=77.0,
    )
    payload = horizon.to_dict()
    assert payload["probability_semantics"] == "uncalibrated_model_tendency"
    assert payload["evidence_confidence"] == 0.75
    assert payload["historical_direction_hit_rate"] is None


def test_no_buy_risk_veto_stays_observable_until_last_scheduled_recheck() -> None:
    model = IntradayTimingModel()
    early = model.assess(
        base_status="NO_BUY",
        current_price=100.0,
        entry_low=None,
        entry_high=None,
        stop_loss=None,
        session_low=99.0,
        session_high=101.0,
        session_vwap=100.0,
        last_5m_return_pct=0.3,
        intraday_volatility_pct=0.6,
        minutes_since_open=40,
        probability_up_1d=0.40,
        probability_up_5d=0.45,
    )
    assert early.action == "NO_BUY"
    assert early.terminal is False
    assert early.recheck_minutes > 0
    assert "observation remains active" in early.rationale

    late = model.assess(
        base_status="NO_BUY",
        current_price=100.0,
        entry_low=None,
        entry_high=None,
        stop_loss=None,
        session_low=99.0,
        session_high=101.0,
        session_vwap=100.0,
        last_5m_return_pct=0.3,
        intraday_volatility_pct=0.6,
        minutes_since_open=151,
        probability_up_1d=0.40,
        probability_up_5d=0.45,
    )
    assert late.terminal is True
    assert late.recheck_minutes == 0
