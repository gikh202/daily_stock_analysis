from __future__ import annotations

from pathlib import Path

import pytest

from src.v6_daily.models import V6Signal
from src.v6_daily.normalized_persistence import NormalizedV6Persistence
from src.v6_daily.normalized_read_store import NormalizedV6ReadStore
from src.v6_daily.read_cutover import compare_daily_payloads, cutover_daily_payload
from src.v6_daily.report import build_daily_payload
from src.v6_daily.store import V6DailyStore
from src.v6_daily.versioned_store import VersionedV6DailyStore


def _signal(history_id: int, *, created: str, trade_date: str, forecast: float, opportunity: float) -> V6Signal:
    return V6Signal(
        analysis_history_id=history_id,
        query_id=f"q-{history_id}",
        code="MSFT",
        analysis_created_at=created,
        baseline_price=100.0 + history_id,
        direction="bullish",
        forecast_score=forecast,
        decision="WATCH",
        quality_score=79.0,
        opportunity_score=opportunity,
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
        diagnostics={"engine_version": "engine-a", "feature_adapter_version": "adapter-a"},
        instrument_type="STOCK",
        effective_trade_date=trade_date,
        horizon_forecasts={
            "5d": {"direction": "bullish", "score": forecast - 4.0, "target_return_pct": 2.0},
            "10d": {"direction": "bullish", "score": forecast, "target_return_pct": 4.0},
            "20d": {"direction": "bullish", "score": forecast - 2.0, "target_return_pct": 6.0},
        },
        context_features={"market": "US"},
    )


def _stores(tmp_path: Path):
    path = tmp_path / "v6.db"
    V6DailyStore(str(path))
    legacy = VersionedV6DailyStore(str(path), active_engine_version="engine-a")
    assert legacy.save_signal(
        _signal(1, created="2026-08-08T02:00:00", trade_date="2026-08-08", forecast=76.0, opportunity=68.0),
        engine_version="engine-a",
    )
    assert legacy.save_signal(
        _signal(2, created="2026-08-11T02:00:00", trade_date="2026-08-11", forecast=82.0, opportunity=74.0),
        engine_version="engine-a",
    )
    first_id = int(legacy.all_signals()[0]["id"])
    assert legacy.save_outcome(
        signal_id=first_id,
        horizon_days=5,
        end_trade_date="2026-08-15",
        start_price=101.0,
        end_price=105.04,
        max_high=106.0,
        min_low=100.0,
        direction="bullish",
        forecast_score=72.0,
        benchmark_spy_return_pct=1.0,
        benchmark_qqq_return_pct=1.5,
    )
    run_stats = {"canonical_signals_seen": 2, "quick_check": "ok"}
    public_context = {"status": {"enabled": False}}
    legacy_payload = build_daily_payload(
        legacy,
        run_stats=run_stats,
        min_samples=3,
        public_context=public_context,
    )
    NormalizedV6Persistence(str(path)).persist_snapshot(
        legacy_payload,
        source_engine_version="engine-a",
        report_date="2026-08-11",
    )
    return path, legacy, legacy_payload, run_stats, public_context


def test_normalized_read_rebuilds_board_deltas_scoreboard_with_exact_business_parity(tmp_path: Path) -> None:
    path, legacy, legacy_payload, run_stats, public_context = _stores(tmp_path)
    normalized = NormalizedV6ReadStore(str(path), active_engine_version="engine-a")
    normalized_payload = build_daily_payload(
        normalized,
        run_stats=run_stats,
        min_samples=3,
        public_context=public_context,
    )

    parity = compare_daily_payloads(legacy_payload, normalized_payload)
    assert parity["parity"] == "exact", parity["differences"]
    assert normalized.counts() == legacy.counts()
    assert normalized.latest_board()[0]["id"] == legacy.latest_board()[0]["id"]
    assert normalized.daily_deltas() == legacy.daily_deltas()
    assert normalized.scoreboard(min_samples=3) == legacy.scoreboard(min_samples=3)
    assert normalized.foreign_key_errors() == []
    assert normalized.quick_check().lower() == "ok"


def test_cutover_selects_normalized_primary_and_manual_legacy_fallback(tmp_path: Path) -> None:
    path, _, legacy_payload, run_stats, public_context = _stores(tmp_path)
    selected, status = cutover_daily_payload(
        legacy_payload,
        db_path=str(path),
        active_engine_version="engine-a",
        run_stats=run_stats,
        min_samples=3,
        public_context=public_context,
        requested_source="normalized",
    )
    assert status["parity"] == "exact"
    assert status["selected_source"] == "normalized_v6_tables"
    assert status["fail_closed"] is True
    assert selected["read_cutover"]["mode"] == "normalized_primary_with_legacy_parity_guard"

    fallback, fallback_status = cutover_daily_payload(
        legacy_payload,
        db_path=str(path),
        active_engine_version="engine-a",
        run_stats=run_stats,
        min_samples=3,
        public_context=public_context,
        requested_source="legacy",
    )
    assert fallback_status["selected_source"] == "legacy_v6_signals_outcomes"
    assert fallback_status["mode"] == "manual_legacy_fallback"
    assert fallback_status["fail_closed"] is False
    assert fallback["board"] == legacy_payload["board"]


def test_normalized_cutover_fails_closed_on_business_drift(tmp_path: Path) -> None:
    path, _, legacy_payload, run_stats, public_context = _stores(tmp_path)
    with NormalizedV6Persistence(str(path)).connect() as conn:
        conn.execute("UPDATE v6_decision_runs SET opportunity_score=99 WHERE source_signal_id=(SELECT MAX(source_signal_id) FROM v6_decision_runs)")

    with pytest.raises(RuntimeError, match="normalized production read parity failed"):
        cutover_daily_payload(
            legacy_payload,
            db_path=str(path),
            active_engine_version="engine-a",
            run_stats=run_stats,
            min_samples=3,
            public_context=public_context,
            requested_source="normalized",
        )
