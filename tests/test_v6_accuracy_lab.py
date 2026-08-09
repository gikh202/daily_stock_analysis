from __future__ import annotations

import sqlite3
from pathlib import Path

from src.alpha_engine.models import AlphaFeatures
from src.v6_daily.accuracy_lab import (
    build_shadow_forecasts,
    run_accuracy_lab,
    simulate_long_trade,
    wilson_interval,
)
from src.v6_daily.models import V6Signal
from src.v6_daily.store import V6DailyStore, mature_outcomes


def test_wilson_interval_and_shadow_profiles_are_deterministic() -> None:
    low, high = wilson_interval(60, 100)
    assert low is not None and high is not None
    assert 50.0 < low < 51.0
    assert 69.0 < high < 70.0

    features = AlphaFeatures(
        trend=78,
        momentum=72,
        relative_strength=75,
        sector_relative_strength=68,
        volume_confirmation=64,
        fundamental_quality=82,
        catalyst=60,
        market_regime=70,
        volatility_risk=35,
        data_quality=90,
    )
    first = build_shadow_forecasts(features, instrument_type="STOCK")
    second = build_shadow_forecasts(features, instrument_type="STOCK")
    assert first == second
    assert set(first) == {"trend_guard", "momentum_focus", "relative_strength_focus"}
    assert all(set(blocks) == {"5d", "10d", "20d"} for blocks in first.values())
    assert first["trend_guard"]["10d"]["weights"] != first["momentum_focus"]["10d"]["weights"]


def test_trade_simulation_uses_conservative_stop_first_on_same_bar() -> None:
    result = simulate_long_trade(
        [
            {
                "date": "2026-01-02",
                "open": 100.0,
                "high": 106.0,
                "low": 94.0,
                "close": 102.0,
            }
        ],
        {"entry_low": 99.0, "entry_high": 100.0, "stop": 95.0, "target": 105.0},
        cost_bps=10.0,
    )
    assert result["status"] == "filled"
    assert result["exit_reason"] == "stop_and_target_same_bar_stop_first"
    assert result["exit_price"] == 95.0
    assert result["return_pct"] < -5.0
    assert result["win"] == 0


def _create_stock_db(path: Path) -> tuple[str, float]:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE stock_daily (
                code TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL
            )
            """
        )
        dates = []
        for index in range(1, 121):
            month = 1 + (index - 1) // 28
            day = 1 + (index - 1) % 28
            date_text = f"2026-{month:02d}-{day:02d}"
            dates.append(date_text)
            msft = 100.0 + index * 0.45
            spy = 500.0 + index * 0.20
            conn.execute(
                "INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?)",
                ("MSFT", date_text, msft - 0.2, msft + 1.2, msft - 1.0, msft, 1_000_000 + index),
            )
            conn.execute(
                "INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?)",
                ("SPY", date_text, spy - 0.1, spy + 0.7, spy - 0.6, spy, 2_000_000 + index),
            )
        conn.commit()
    finally:
        conn.close()
    analysis_index = 70
    return dates[analysis_index - 1], 100.0 + analysis_index * 0.45


def test_accuracy_lab_persists_shadow_outcomes_and_execution_metrics(tmp_path: Path) -> None:
    stock_db = tmp_path / "stock.db"
    analysis_date, baseline = _create_stock_db(stock_db)
    v6_db = tmp_path / "v6.db"
    store = V6DailyStore(str(v6_db))

    horizon_forecasts = {
        "5d": {"horizon_days": 5, "score": 72.0, "direction": "bullish", "evidence_coverage": 0.9},
        "10d": {"horizon_days": 10, "score": 74.0, "direction": "bullish", "evidence_coverage": 0.9},
        "20d": {"horizon_days": 20, "score": 76.0, "direction": "bullish", "evidence_coverage": 0.9},
    }
    features = {
        "trend": 78.0,
        "momentum": 74.0,
        "relative_strength": 72.0,
        "sector_relative_strength": 68.0,
        "volume_confirmation": 65.0,
        "fundamental_quality": 82.0,
        "catalyst": 60.0,
        "market_regime": 70.0,
        "volatility_risk": 30.0,
        "data_quality": 90.0,
    }
    signal = V6Signal(
        analysis_history_id=1,
        query_id="q-1",
        code="MSFT",
        analysis_created_at=f"{analysis_date} 22:30:00",
        baseline_price=baseline,
        direction="bullish",
        forecast_score=74.0,
        decision="BUY_SETUP",
        quality_score=82.0,
        opportunity_score=80.0,
        risk_score=30.0,
        evidence_coverage=0.90,
        market_regime="risk_on",
        market_breadth="broad",
        model_used="deepseek/deepseek-v4-flash",
        llm_health="healthy",
        features=features,
        trade_plan={
            "entry_zone": [baseline - 1.0, baseline + 0.5],
            "stop_loss": baseline - 4.0,
            "targets": [baseline + 5.0, baseline + 8.0],
            "max_position_pct": 0.10,
            "risk_reward": 1.5,
        },
        instrument_type="STOCK",
        effective_trade_date=analysis_date,
        horizon_forecasts=horizon_forecasts,
    )
    assert store.save_signal(signal, engine_version="v6.1-test")
    matured = mature_outcomes(store, str(stock_db))
    assert matured["evaluated"] == 3

    report_dir = tmp_path / "reports"
    payload = run_accuracy_lab(
        str(v6_db),
        str(stock_db),
        report_dir=report_dir,
        min_samples=3,
        promotion_min_samples=3,
        cost_bps=10.0,
        max_holding_bars=20,
    )

    assert payload["policy"]["auto_promotion"] is False
    assert payload["policy"]["auto_weight_tuning"] is False
    assert payload["run"]["new_shadow_forecasts"] == 9
    assert payload["run"]["new_shadow_outcomes"] == 9
    assert payload["run"]["new_trade_outcomes"] == 1
    assert len(payload["champion"]) == 3
    assert len(payload["challengers"]) == 9
    assert payload["strategy"]["evaluated_plans"] == 1
    assert (report_dir / "v6_accuracy_lab.json").is_file()
    assert (report_dir / "v6_accuracy_lab.md").is_file()

    # Re-running is idempotent; it must not duplicate shadow samples.
    second = run_accuracy_lab(
        str(v6_db),
        str(stock_db),
        report_dir=report_dir,
        min_samples=3,
        promotion_min_samples=3,
        cost_bps=10.0,
        max_holding_bars=20,
    )
    assert second["run"]["new_shadow_forecasts"] == 0
    assert second["run"]["new_shadow_outcomes"] == 0
    assert second["run"]["new_trade_outcomes"] == 0
