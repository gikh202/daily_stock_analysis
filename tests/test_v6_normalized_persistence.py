from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.v6_daily.models import V6Signal
from src.v6_daily.normalized_persistence import NormalizedV6Persistence
from src.v6_daily.store import V6DailyStore
from src.v6_daily.versioned_store import VersionedV6DailyStore


def _signal(*, history_id: int = 1, code: str = "MSFT", decision: str = "WATCH") -> V6Signal:
    return V6Signal(
        analysis_history_id=history_id,
        query_id=f"q-{history_id}",
        code=code,
        analysis_created_at="2026-08-11T02:00:00",
        baseline_price=100.0,
        direction="bullish",
        forecast_score=82.0,
        decision=decision,
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
        effective_trade_date="2026-08-11",
        horizon_forecasts={
            "5d": {"direction": "bullish", "score": 75.0, "target_return_pct": 2.0},
            "10d": {"direction": "bullish", "score": 82.0, "target_return_pct": 4.0},
            "20d": {"direction": "bullish", "score": 78.0, "target_return_pct": 6.0},
        },
        context_features={"market": "US"},
    )


def _payload(store: VersionedV6DailyStore) -> dict:
    return {
        "version": "engine-a",
        "generated_at": "2026-08-11T03:00:00",
        "run": {"canonical_signals_seen": 1},
        "board": store.latest_board(),
    }


def test_normalized_persistence_dual_writes_exact_domain_parity_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    # Create the legacy schema first, then exercise the versioned migration used
    # by production before the normalized shadow tables are written.
    V6DailyStore(str(path))
    store = VersionedV6DailyStore(str(path), active_engine_version="engine-a")
    signal = _signal()
    assert store.save_signal(signal, engine_version="engine-a") is True

    source_signal = store.all_signals()[0]
    signal_id = int(source_signal["id"])
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

    normalized = NormalizedV6Persistence(str(path))
    payload = _payload(store)
    first = normalized.persist_snapshot(
        payload,
        source_engine_version="engine-a",
        report_date="2026-08-11",
        run_mode="LIVE",
        metadata={"decision_source": "FinalDecisionPacket"},
    )
    again = normalized.persist_snapshot(
        payload,
        source_engine_version="engine-a",
        report_date="2026-08-11",
        run_mode="LIVE",
        metadata={"decision_source": "FinalDecisionPacket"},
    )

    assert first["mode"] == "legacy_primary_dual_write"
    assert first["parity"] == "exact"
    assert first["source_signals"] == 1
    assert first["forecast_runs"] == 1
    assert first["horizon_forecasts"] == 3
    assert first["decision_runs"] == 1
    assert first["execution_plans"] == 1
    assert first["source_outcomes"] == 1
    assert first["forecast_outcomes"] == 1
    assert first["manifest_inserted"] is True
    assert again["manifest_inserted"] is False
    assert again["manifest_id"] == first["manifest_id"]
    assert again["source_snapshot_hash"] == first["source_snapshot_hash"]

    with normalized.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM v6_run_manifests").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_horizon_forecasts").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM v6_decision_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_execution_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0] == 1

        forecast = conn.execute("SELECT * FROM v6_forecast_runs").fetchone()
        assert forecast["source_signal_id"] == signal_id
        assert forecast["engine_version"] == "engine-a"
        assert forecast["symbol"] == "MSFT"
        assert forecast["run_manifest_id"] == first["manifest_id"]

        decision = conn.execute("SELECT * FROM v6_decision_runs").fetchone()
        assert decision["decision_schema_version"] == "decision-packet-v1"
        assert decision["assessment_scope"] == "v6_deterministic_pre_fusion"
        assert decision["assessment_is_final"] == 0
        assert decision["deterministic_decision"] == "WATCH"
        assert decision["assessment_verdict"] == "watch"
        assert decision["worth_buying"] is None
        assert decision["execution_status"] == "waiting_confirmation"
        assert decision["execution_actionable"] == 1
        packet = json.loads(decision["decision_packet_json"])
        assert packet["assessment"]["is_final"] is False
        assert packet["execution"]["has_active_plan"] is True

        plan = conn.execute("SELECT * FROM v6_execution_plans").fetchone()
        assert plan["entry_low"] == 100.0
        assert plan["entry_high"] == 102.0
        assert plan["stop_loss"] == 96.0
        assert plan["has_active_plan"] == 1
        assert plan["actionable"] == 1

        outcome = conn.execute("SELECT * FROM v6_forecast_outcomes").fetchone()
        assert outcome["source_signal_id"] == signal_id
        assert outcome["horizon_days"] == 5
        assert outcome["return_pct"] == 4.0
        assert outcome["excess_vs_spy_pct"] == 3.0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    assert normalized.quick_check().lower() == "ok"


def test_normalized_persistence_scopes_source_rows_by_engine_version(tmp_path: Path) -> None:
    path = tmp_path / "v6_multi.db"
    V6DailyStore(str(path))
    store = VersionedV6DailyStore(str(path), active_engine_version="engine-a")
    assert store.save_signal(_signal(history_id=1), engine_version="engine-a") is True

    challenger = _signal(history_id=1)
    challenger = V6Signal(
        **{
            **challenger.__dict__,
            "diagnostics": {
                "engine_version": "engine-b",
                "feature_adapter_version": "adapter-b",
            },
        }
    )
    assert store.save_signal(challenger, engine_version="engine-b") is True

    normalized = NormalizedV6Persistence(str(path))
    summary = normalized.persist_snapshot(
        _payload(store),
        source_engine_version="engine-a",
        report_date="2026-08-11",
    )
    assert summary["source_signals"] == 1
    assert summary["forecast_runs"] == 1

    with normalized.connect() as conn:
        engines = {
            str(row[0])
            for row in conn.execute("SELECT DISTINCT engine_version FROM v6_forecast_runs").fetchall()
        }
    assert engines == {"engine-a"}
