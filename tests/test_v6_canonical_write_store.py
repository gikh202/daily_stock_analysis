from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.v6_daily.canonical_write_store import CanonicalV6WriteStore
from src.v6_daily.models import V6Signal
from src.v6_daily.store import V6DailyStore
from src.v6_daily.versioned_store import VersionedV6DailyStore


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


def test_canonical_write_inserts_normalized_first_and_projects_same_identity(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    V6DailyStore(str(path))
    store = CanonicalV6WriteStore(str(path), active_engine_version="engine-a")

    assert store.save_signal(_signal(), engine_version="engine-a") is True
    rows = store.all_signals()
    assert len(rows) == 1
    signal_id = int(rows[0]["id"])

    with store.connect() as conn:
        forecast = conn.execute(
            "SELECT source_signal_id FROM v6_forecast_runs WHERE engine_version='engine-a'"
        ).fetchone()
        legacy = conn.execute(
            "SELECT id, engine_version FROM v6_signals WHERE engine_version='engine-a'"
        ).fetchone()
        assert forecast is not None and legacy is not None
        assert int(forecast["source_signal_id"]) == signal_id
        assert int(legacy["id"]) == signal_id
        assert conn.execute("SELECT COUNT(*) FROM v6_decision_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_execution_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_horizon_forecasts").fetchone()[0] == 3

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

    with store.connect() as conn:
        normalized = conn.execute(
            "SELECT source_outcome_id, source_signal_id FROM v6_forecast_outcomes"
        ).fetchone()
        legacy = conn.execute("SELECT id, signal_id FROM v6_outcomes").fetchone()
        assert normalized is not None and legacy is not None
        assert int(normalized["source_outcome_id"]) == int(legacy["id"])
        assert int(normalized["source_signal_id"]) == int(legacy["signal_id"]) == signal_id

    status = store.write_status()
    assert status["mode"] == "normalized_primary_legacy_projection"
    assert status["canonical_source"] == "normalized_v6_tables"
    assert status["legacy_role"] == "compatibility_projection_only"
    assert status["parity"] == "exact"
    assert status["canonical_signals"] == status["legacy_signal_projections"] == 1
    assert status["canonical_outcomes"] == status["legacy_outcome_projections"] == 1
    assert status["foreign_key_errors"] == 0
    assert status["quick_check"].lower() == "ok"


def test_canonical_write_rolls_back_when_legacy_projection_fails(tmp_path: Path) -> None:
    path = tmp_path / "rollback.db"
    V6DailyStore(str(path))
    store = CanonicalV6WriteStore(str(path), active_engine_version="engine-a")
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_legacy_signal_projection
            BEFORE INSERT ON v6_signals
            BEGIN
                SELECT RAISE(ABORT, 'legacy projection rejected');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="legacy projection rejected"):
        store.save_signal(_signal(), engine_version="engine-a")

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM v6_decision_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM v6_execution_plans").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM v6_horizon_forecasts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM v6_signals").fetchone()[0] == 0


def test_write_primary_bootstraps_existing_legacy_history_without_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "bootstrap.db"
    V6DailyStore(str(path))
    legacy = VersionedV6DailyStore(str(path), active_engine_version="engine-a")
    signal = _signal()
    assert legacy.save_signal(signal, engine_version="engine-a") is True
    legacy_id = int(legacy.all_signals()[0]["id"])
    assert legacy.save_outcome(
        signal_id=legacy_id,
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

    store = CanonicalV6WriteStore(str(path), active_engine_version="engine-a")
    assert store.bootstrap_summary["performed"] is True
    assert store.bootstrap_summary["parity"] == "exact"
    assert store.has_analysis_history_version(1) is True
    assert store.has_signal_key("MSFT", "2026-08-11", "engine-a") is True
    assert store.save_signal(signal, engine_version="engine-a") is False

    status = store.write_status()
    assert status["parity"] == "exact"
    assert status["canonical_signals"] == status["legacy_signal_projections"] == 1
    assert status["canonical_outcomes"] == status["legacy_outcome_projections"] == 1
