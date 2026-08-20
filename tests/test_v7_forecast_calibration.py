from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.forecasting.history import ForecastHistory


def _db(path: Path) -> None:
    conn=sqlite3.connect(path); conn.executescript("""
    CREATE TABLE v6_forecast_runs (id INTEGER PRIMARY KEY, engine_version TEXT, market_regime TEXT, effective_trade_date TEXT);
    CREATE TABLE v6_horizon_forecasts (id INTEGER PRIMARY KEY, forecast_run_id INTEGER, horizon_days INTEGER, score REAL, payload_json TEXT);
    CREATE TABLE v6_forecast_outcomes (id INTEGER PRIMARY KEY, forecast_run_id INTEGER, horizon_days INTEGER, end_trade_date TEXT, return_pct REAL, mfe_pct REAL, mae_pct REAL, excess_vs_spy_pct REAL);
    """)
    for idx in range(1,41):
        p=.65 if idx<=30 else .35; ret=2.0 if idx<=30 else -1.5
        conn.execute("INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",(idx,"v7-test","risk_on",f"2026-01-{(idx%20)+1:02d}"))
        conn.execute("INSERT INTO v6_horizon_forecasts(forecast_run_id,horizon_days,score,payload_json) VALUES (?,?,?,?)",(idx,5,p*100,json.dumps({"probability_up":p,"challenger_probability_up":.60})))
        conn.execute("INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,end_trade_date,return_pct,mfe_pct,mae_pct,excess_vs_spy_pct) VALUES (?,?,?,?,?,?,?)",(idx,5,f"2026-02-{(idx%20)+1:02d}",ret,max(ret,3.0),min(ret,-1.0),ret-.5))
    conn.commit(); conn.close()


def test_calibration_uses_only_strictly_prior_outcomes(tmp_path: Path) -> None:
    path=tmp_path/"forecast.db"; _db(path); history=ForecastHistory(str(path),minimum_samples=20,minimum_regime_samples=10)
    profile=history.calibration(as_of_date="2026-03-01",horizon_days=5,raw_probability_up=.65,regime="risk_on")
    assert profile.samples>=20 and profile.status=="mature" and .5<profile.probability_up<.95
    assert profile.brier_score is not None and profile.log_loss is not None and profile.ece is not None
    no_future=history.calibration(as_of_date="2026-01-15",horizon_days=5,raw_probability_up=.65,regime="risk_on")
    assert no_future.samples==0 and no_future.status=="prior_only"


def test_challenger_cannot_promote_without_forward_sample_floor(tmp_path: Path) -> None:
    path=tmp_path/"forecast.db"; _db(path); history=ForecastHistory(str(path))
    selection=history.select_champion(as_of_date="2026-03-01",horizon_days=5,regime="risk_on",min_promotion_samples=200)
    assert selection["champion_model"]=="calibrated_ensemble" and selection["status"] in {"observing","cold_start"}
