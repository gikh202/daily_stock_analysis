from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.audit_feature_redundancy import run


def test_audit_flags_highly_correlated_features(tmp_path: Path) -> None:
    db = tmp_path / "v6.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE v6_forecast_runs (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                effective_trade_date TEXT,
                features_json TEXT
            );
            CREATE TABLE v6_forecast_outcomes (
                id INTEGER PRIMARY KEY,
                forecast_run_id INTEGER,
                horizon_days INTEGER,
                return_pct REAL,
                directional_hit INTEGER
            );
            """
        )
        for index in range(40):
            trend = 20.0 + index * 1.5
            features = {
                "trend": trend,
                "momentum": trend,
                "relative_strength": 30.0 + index,
                "macro_risk": 70.0 - index,
            }
            realized = -2.0 if trend < 50.0 else 2.0
            conn.execute(
                "INSERT INTO v6_forecast_runs(id,symbol,effective_trade_date,features_json) VALUES (?,?,?,?)",
                (index + 1, "TEST", f"2026-07-{(index % 28) + 1:02d}", json.dumps(features)),
            )
            conn.execute(
                "INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,return_pct,directional_hit) VALUES (?,?,?,?)",
                (index + 1, 5, realized, 1),
            )
        conn.commit()
    finally:
        conn.close()

    payload = run(db, min_pair_samples=20, redundancy_threshold=0.90)
    assert payload["samples"] == 40
    assert any(
        {item["left"], item["right"]} == {"trend", "momentum"}
        and abs(item["correlation"]) >= 0.99
        for item in payload["redundant_pairs"]
    )
    assert payload["leave_one_out"]["trend"]["samples"] == 40
    assert "production weights automatically" in payload["policy"]
