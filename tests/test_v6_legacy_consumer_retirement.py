from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from src.v6_daily.legacy_retirement import evaluate_legacy_retirement
from src.v6_daily.normalized_accuracy_lab import (
    SHADOW_FORECAST_TABLE,
    SHADOW_OUTCOME_TABLE,
    TRADE_OUTCOME_TABLE,
    ensure_normalized_accuracy_lab_schema,
    run_normalized_accuracy_lab,
)
from src.v6_daily.normalized_cutover import cutover_daily_payload
from src.v6_daily.normalized_manifest_store import NormalizedV6ManifestStore
from src.v6_daily.normalized_persistence import NormalizedV6Persistence
from src.v6_daily.normalized_read_store import NormalizedV6ReadStore
from src.v6_daily.report import build_daily_payload


ENGINE = "engine-a"


def _create_normalized_fixture(tmp_path: Path) -> tuple[Path, Path]:
    v6_path = tmp_path / "v6.db"
    stock_path = tmp_path / "stock.db"
    NormalizedV6Persistence(str(v6_path))

    features = {
        "trend": 72.0,
        "momentum": 68.0,
        "relative_strength": 66.0,
        "volume_confirmation": 61.0,
        "market_regime": 64.0,
        "volatility_risk": 30.0,
        "data_quality": 100.0,
    }
    decision_packet = {
        "schema_version": "decision-packet-v1",
        "identity": {
            "symbol": "MSFT",
            "instrument_type": "STOCK",
            "effective_trade_date": "2026-01-02",
        },
        "forecast": {
            "direction": "bullish",
            "score": 65.0,
            "horizons": {
                "5d": {
                    "horizon_days": 5,
                    "direction": "bullish",
                    "score": 65.0,
                    "evidence_coverage": 1.0,
                },
                "10d": {
                    "horizon_days": 10,
                    "direction": "bullish",
                    "score": 64.0,
                    "evidence_coverage": 1.0,
                },
                "20d": {
                    "horizon_days": 20,
                    "direction": "neutral",
                    "score": 58.0,
                    "evidence_coverage": 1.0,
                },
            },
        },
        "scores": {
            "quality": 70.0,
            "opportunity": 72.0,
            "risk": 28.0,
            "evidence_coverage": 1.0,
        },
        "assessment": {
            "scope": "v6_deterministic_pre_fusion",
            "is_final": False,
            "verdict": "buy_by_plan",
            "worth_buying": True,
        },
        "execution": {
            "status": "executable",
            "action": "BUY_SETUP",
            "entry_zone": [99.0, 101.0],
            "stop_loss": 95.0,
            "targets": [110.0],
            "max_position_pct": 10.0,
            "risk_reward": 2.0,
            "confirmations": [],
            "invalidations": [],
            "actionable": True,
            "has_active_plan": True,
        },
        "evidence": {
            "catalysts": ["fixture catalyst"],
            "risks": ["fixture risk"],
            "limitations": [],
        },
        "provenance": {
            "engine_version": ENGINE,
            "feature_adapter_version": "fixture-v1",
        },
    }
    plan = {
        "status": "executable",
        "action": "BUY_SETUP",
        "entry_zone": [99.0, 101.0],
        "stop_loss": 95.0,
        "targets": [110.0],
        "max_position_pct": 10.0,
        "risk_reward": 2.0,
        "confirmations": [],
        "invalidations": [],
        "actionable": True,
        "has_active_plan": True,
    }

    conn = sqlite3.connect(str(v6_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        cursor = conn.execute(
            """
            INSERT INTO v6_forecast_runs(
                source_signal_id, run_manifest_id, analysis_history_id, query_id,
                symbol, instrument_type, effective_trade_date,
                analysis_created_at, v6_created_at, engine_version,
                direction, forecast_score, baseline_price, evidence_coverage,
                market_regime, market_breadth, llm_health,
                feature_adapter_version, features_json,
                context_features_json, diagnostics_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1,
                None,
                101,
                "q1",
                "MSFT",
                "STOCK",
                "2026-01-02",
                "2026-01-02T12:00:00+00:00",
                "2026-01-02T12:00:00+00:00",
                ENGINE,
                "bullish",
                65.0,
                100.0,
                1.0,
                "risk_on",
                "broad",
                "not_used",
                "fixture-v1",
                json.dumps(features),
                "{}",
                "{}",
            ),
        )
        forecast_run_id = int(cursor.lastrowid)
        for key, days, direction, score in (
            ("5d", 5, "bullish", 65.0),
            ("10d", 10, "bullish", 64.0),
            ("20d", 20, "neutral", 58.0),
        ):
            conn.execute(
                """
                INSERT INTO v6_horizon_forecasts(
                    forecast_run_id, horizon_key, horizon_days,
                    direction, score, expected_return_pct, payload_json
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    forecast_run_id,
                    key,
                    days,
                    direction,
                    score,
                    None,
                    json.dumps(decision_packet["forecast"]["horizons"][key]),
                ),
            )

        cursor = conn.execute(
            """
            INSERT INTO v6_decision_runs(
                source_signal_id, forecast_run_id, run_manifest_id,
                decision_schema_version, assessment_scope, assessment_is_final,
                deterministic_decision, assessment_verdict, worth_buying,
                quality_score, opportunity_score, risk_score, evidence_coverage,
                execution_status, execution_actionable, decision_packet_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1,
                forecast_run_id,
                None,
                "decision-packet-v1",
                "v6_deterministic_pre_fusion",
                0,
                "BUY_SETUP",
                "buy_by_plan",
                1,
                70.0,
                72.0,
                28.0,
                1.0,
                "executable",
                1,
                json.dumps(decision_packet),
            ),
        )
        decision_run_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO v6_execution_plans(
                decision_run_id, status, action, entry_low, entry_high,
                stop_loss, targets_json, max_position_pct, risk_reward,
                confirmations_json, invalidations_json,
                has_active_plan, actionable, plan_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                decision_run_id,
                "executable",
                "BUY_SETUP",
                99.0,
                101.0,
                95.0,
                "[110.0]",
                10.0,
                2.0,
                "[]",
                "[]",
                1,
                1,
                json.dumps(plan),
            ),
        )
        conn.execute(
            """
            INSERT INTO v6_forecast_outcomes(
                source_outcome_id, forecast_run_id, source_signal_id,
                horizon_days, evaluated_at, end_trade_date,
                start_price, end_price, return_pct, mfe_pct, mae_pct,
                directional_hit, forecast_score, direction_used,
                benchmark_spy_return_pct, benchmark_qqq_return_pct,
                excess_vs_spy_pct, excess_vs_qqq_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1,
                forecast_run_id,
                1,
                5,
                "2026-01-09T12:00:00+00:00",
                "2026-01-09",
                100.0,
                105.0,
                5.0,
                6.0,
                -1.0,
                1,
                65.0,
                "bullish",
                2.0,
                2.5,
                3.0,
                2.5,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    stock = sqlite3.connect(str(stock_path))
    try:
        stock.execute(
            """
            CREATE TABLE stock_daily(
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY(code, date)
            )
            """
        )
        start = date(2026, 1, 2)
        for offset in range(30):
            day = (start + timedelta(days=offset)).isoformat()
            msft = 100.0 + offset * 0.7
            spy = 500.0 + offset * 0.4
            for code, close in (("MSFT", msft), ("SPY", spy), ("QQQ", 600.0 + offset * 0.5)):
                stock.execute(
                    "INSERT INTO stock_daily(code,date,open,high,low,close,volume) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (code, day, close, close * 1.02, close * 0.98, close, 1_000_000.0),
                )
        stock.commit()
    finally:
        stock.close()

    # The fixture deliberately proves Stage 9 without creating legacy V6 tables.
    with sqlite3.connect(str(v6_path)) as check:
        tables = {
            str(row[0])
            for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "v6_signals" not in tables
    assert "v6_outcomes" not in tables
    return v6_path, stock_path


def test_normalized_accuracy_lab_has_no_legacy_fk_or_read_dependency(tmp_path: Path) -> None:
    v6_path, stock_path = _create_normalized_fixture(tmp_path)
    schema = ensure_normalized_accuracy_lab_schema(v6_path)
    assert schema["source"] == "normalized_v6_tables"
    assert schema["legacy_signal_reads"] == 0
    assert schema["legacy_outcome_reads"] == 0

    with sqlite3.connect(str(v6_path)) as conn:
        for table in (SHADOW_FORECAST_TABLE, SHADOW_OUTCOME_TABLE, TRADE_OUTCOME_TABLE):
            targets = {
                str(row[2])
                for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            }
            assert "v6_signals" not in targets
            assert "v6_outcomes" not in targets

    payload = run_normalized_accuracy_lab(
        v6_path,
        stock_path,
        report_dir=tmp_path / "reports",
        active_engine_version=ENGINE,
        min_samples=3,
        promotion_min_samples=3,
        max_holding_bars=5,
    )
    assert payload["source"]["mode"] == "normalized_only"
    assert payload["source"]["legacy_signal_reads"] == 0
    assert payload["source"]["legacy_outcome_reads"] == 0
    assert payload["run"]["legacy_consumer"] is False


def test_normalized_manifest_and_cutover_work_without_legacy_tables(tmp_path: Path) -> None:
    v6_path, _ = _create_normalized_fixture(tmp_path)
    store = NormalizedV6ReadStore(str(v6_path), active_engine_version=ENGINE)
    run_stats = {"quick_check": "ok", "active_engine_version": ENGINE}
    reference = build_daily_payload(
        store,
        run_stats=run_stats,
        min_samples=3,
        public_context={},
    )
    reference["generated_at"] = "2026-01-10T00:00:00+00:00"
    reference["version"] = ENGINE

    manifest = NormalizedV6ManifestStore(str(v6_path)).persist_snapshot(
        reference,
        source_engine_version=ENGINE,
        report_date="2026-01-10",
        run_mode="LIVE",
        metadata={"test": True},
    )
    assert manifest["parity"] == "exact"
    assert manifest["source_mode"] == "normalized_only"
    assert manifest["legacy_reference_used"] is False
    assert manifest["source_signals"] == 1
    assert manifest["source_outcomes"] == 1

    selected, metadata = cutover_daily_payload(
        reference,
        db_path=str(v6_path),
        active_engine_version=ENGINE,
        run_stats=run_stats,
        min_samples=3,
        public_context={},
        requested_source="normalized",
    )
    assert selected["board"][0]["code"] == "MSFT"
    assert metadata["selected_source"] == "normalized_v6_tables"
    assert metadata["mode"] == "normalized_primary_self_consistency_guard"
    assert metadata["parity"] == "exact"
    assert metadata["legacy_reference_used"] is False
    assert metadata["legacy_consumer_count"] == 0


def test_legacy_retirement_ready_even_when_legacy_tables_are_absent(tmp_path: Path) -> None:
    v6_path, _ = _create_normalized_fixture(tmp_path)
    ensure_normalized_accuracy_lab_schema(v6_path)
    readiness = evaluate_legacy_retirement(v6_path)

    assert readiness["status"] == "retirement_ready"
    assert readiness["projection_retirement_ready"] is True
    assert readiness["legacy_projection_required_by_active_consumers"] is False
    assert readiness["legacy_consumer_count"] == 0
    assert readiness["legacy_fk_dependencies"] == []
    assert readiness["legacy_tables_present"] == []
    assert readiness["legacy_projection_enabled"] is False


def test_stage9_active_modules_do_not_query_legacy_v6_fact_tables() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "src/v6_daily/normalized_accuracy_lab.py",
        root / "src/v6_daily/normalized_manifest_store.py",
        root / "src/v6_daily/normalized_cutover.py",
        root / "scripts/run_v6_daily_stage9.py",
    )
    banned = (
        "FROM v6_signals",
        "JOIN v6_signals",
        "FROM v6_outcomes",
        "JOIN v6_outcomes",
        "REFERENCES v6_signals",
        "REFERENCES v6_outcomes",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, (path, token)
