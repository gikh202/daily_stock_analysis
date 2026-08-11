from __future__ import annotations

import sqlite3
from pathlib import Path

from src.v6_daily.legacy_write_guard import (
    assert_legacy_facts_unchanged,
    snapshot_legacy_facts,
)
from src.v6_daily.models import V6Signal
from src.v6_daily.normalized_write_store import NormalizedOnlyV6WriteStore
from src.v6_daily.store import V6DailyStore


def _signal(history_id: int = 1, *, trade_date: str = "2026-08-11") -> V6Signal:
    return V6Signal(
        analysis_history_id=history_id,
        query_id=f"q-{history_id}",
        code="MSFT",
        analysis_created_at=f"{trade_date}T02:00:00",
        baseline_price=100.0,
        direction="bullish",
        forecast_score=82.0,
        decision="WATCH",
        quality_score=79.0,
        opportunity_score=74.0,
        risk_score=38.0,
        evidence_coverage=0.8,
        market_regime="risk_on",
        market_breadth="broad",
        model_used="deepseek/test",
        llm_health="healthy",
        features={"trend": 88.0, "momentum": 72.0},
        trade_plan={
            "entry_zone": [100.0, 102.0],
            "stop_loss": 96.0,
            "targets": [110.0, 115.0],
            "risk_reward": 2.0,
            "max_position_pct": 0.1,
            "confirmations": ["hold support"],
            "invalidation": ["close below stop"],
        },
        catalysts=("cloud growth",),
        risks=("valuation",),
        limitations=("sample size",),
        diagnostics={
            "engine_version": "engine-a",
            "feature_adapter_version": "adapter-a",
        },
        instrument_type="STOCK",
        effective_trade_date=trade_date,
        horizon_forecasts={
            "5d": {"direction": "bullish", "score": 75.0, "target_return_pct": 2.0},
            "10d": {"direction": "bullish", "score": 82.0, "target_return_pct": 4.0},
            "20d": {"direction": "bullish", "score": 78.0, "target_return_pct": 6.0},
        },
        context_features={"market": "US"},
    )


def test_normalized_only_writer_does_not_create_legacy_fact_tables(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    store = NormalizedOnlyV6WriteStore(str(path), active_engine_version="engine-a")

    assert store.save_signal(_signal(), engine_version="engine-a") is True
    signal_id = int(store.all_signals()[0]["id"])
    assert signal_id == 1
    assert store.save_outcome(
        signal_id=signal_id,
        horizon_days=5,
        end_trade_date="2026-08-18",
        start_price=100.0,
        end_price=104.0,
        max_high=105.0,
        min_low=99.0,
        direction="bullish",
        forecast_score=75.0,
        benchmark_spy_return_pct=1.0,
        benchmark_qqq_return_pct=1.5,
    ) is True

    with sqlite3.connect(str(path)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "v6_signals" not in tables
        assert "v6_outcomes" not in tables
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_decision_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_execution_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0] == 1

    status = store.write_status()
    assert status["mode"] == "normalized_only_no_legacy_projection"
    assert status["identity_source"] == "normalized_sequence_only"
    assert status["legacy_projection_enabled"] is False
    assert status["legacy_projection_writes"] == 0
    assert status["legacy_signal_projection_writes"] == 0
    assert status["legacy_outcome_projection_writes"] == 0
    assert status["automatic_legacy_bootstrap"] is False
    assert status["parity"] == "exact"
    assert status["foreign_key_errors"] == 0
    assert status["quick_check"].lower() == "ok"


def test_normalized_identity_ignores_legacy_max_and_legacy_rows_stay_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    V6DailyStore(str(path))
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            INSERT INTO v6_signals(
                id, analysis_history_id, query_id, code, analysis_created_at,
                v6_created_at, engine_version, direction, forecast_score,
                decision, quality_score, opportunity_score, risk_score,
                evidence_coverage, baseline_price, market_regime, market_breadth,
                model_used, llm_health, features_json, trade_plan_json,
                catalysts_json, risks_json, limitations_json, diagnostics_json,
                instrument_type, effective_trade_date, signal_key,
                horizon_forecasts_json, context_features_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                999,
                999,
                "legacy-q",
                "LEGACY",
                "2025-01-01T00:00:00",
                "2025-01-01T00:00:00",
                "legacy-engine",
                "neutral",
                50.0,
                "WATCH",
                50.0,
                50.0,
                50.0,
                1.0,
                100.0,
                "unknown",
                "unknown",
                "legacy",
                "healthy",
                "{}",
                "{}",
                "[]",
                "[]",
                "[]",
                "{}",
                "STOCK",
                "2025-01-01",
                "legacy-key-999",
                "{}",
                "{}",
            ),
        )
        conn.commit()

    before = snapshot_legacy_facts(path)
    store = NormalizedOnlyV6WriteStore(str(path), active_engine_version="engine-a")
    assert store.save_signal(_signal(), engine_version="engine-a") is True
    assert int(store.all_signals()[0]["id"]) == 1
    after = snapshot_legacy_facts(path)
    guard = assert_legacy_facts_unchanged(before, after)

    assert guard["status"] == "unchanged"
    assert guard["legacy_writes_detected"] is False
    assert guard["legacy_projection_writes"] == 0
    assert before["tables"]["v6_signals"]["rows"] == 1
    assert before["tables"]["v6_signals"]["sha256"] == after["tables"]["v6_signals"]["sha256"]


def test_legacy_insert_trigger_cannot_block_normalized_only_writer(tmp_path: Path) -> None:
    path = tmp_path / "trigger.db"
    V6DailyStore(str(path))
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_any_legacy_signal_write
            BEFORE INSERT ON v6_signals
            BEGIN
                SELECT RAISE(ABORT, 'legacy writes forbidden');
            END;
            """
        )
        conn.commit()

    store = NormalizedOnlyV6WriteStore(str(path), active_engine_version="engine-a")
    assert store.save_signal(_signal(), engine_version="engine-a") is True
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM v6_signals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 1
