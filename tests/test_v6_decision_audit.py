from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.v6_daily.decision_audit import FinalDecisionAuditStore
from src.v6_daily.final_decision_service import build_final_decision_packets
from src.v6_daily.store import V6DailyStore
from src.v6_daily.versioned_store import VersionedV6DailyStore


def _payload(*, decision: str = "WATCH", engine_version: str = "v6-a") -> dict:
    return {
        "version": engine_version,
        "generated_at": "2026-08-11T03:00:00",
        "board": [
            {
                "id": 7,
                "analysis_history_id": 1,
                "query_id": "q-1",
                "engine_version": engine_version,
                "code": "MSFT",
                "instrument_type": "STOCK",
                "effective_trade_date": "2026-08-11",
                "decision": decision,
                "direction": "bullish",
                "forecast_score": 82.0,
                "opportunity_score": 74.0,
                "risk_score": 38.0,
                "evidence_coverage": 0.8,
                "trade_plan": {
                    "entry_zone": [100.0, 102.0],
                    "stop_loss": 96.0,
                    "targets": [110.0, 115.0],
                    "risk_reward": 2.0,
                    "max_position_pct": 0.1,
                },
                "catalysts": ["cloud growth"],
                "risks": ["valuation"],
            }
        ],
    }


def _v4_record(*, operation: str = "观望") -> dict:
    raw = {
        "name": "Microsoft",
        "trend_prediction": "看多",
        "operation_advice": operation,
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": "bullish",
                    "expected_return_pct": 4.0,
                    "rationale": "trend remains constructive",
                }
            },
        },
        "dashboard": {
            "intelligence": {
                "positive_catalysts": ["earnings resilience"],
                "risk_alerts": ["valuation risk"],
            },
            "phase_decision": {
                "phase_context": {
                    "phase": "trading",
                    "is_trading_day": True,
                    "effective_daily_bar_date": "2026-08-11",
                },
                "watch_conditions": ["hold support"],
            },
            "signal_attribution": {
                "strongest_bullish_signal": "trend",
                "strongest_bearish_signal": "valuation",
            },
        },
    }
    return {
        "id": 1,
        "query_id": "q-1",
        "code": "MSFT",
        "created_at": "2026-08-11 02:00:00",
        "raw_result": json.dumps(raw, ensure_ascii=False),
    }


def _insert_signal(
    conn: sqlite3.Connection,
    *,
    history_id: int,
    engine: str,
    effective_date: str,
    created_at: str,
    signal_key: str,
) -> None:
    conn.execute(
        """
        INSERT INTO v6_signals(
            analysis_history_id, code, analysis_created_at, v6_created_at,
            engine_version, direction, decision, evidence_coverage,
            baseline_price, llm_health, features_json, trade_plan_json,
            catalysts_json, risks_json, limitations_json, diagnostics_json,
            effective_trade_date, signal_key
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            history_id,
            "MSFT",
            created_at,
            created_at,
            engine,
            "bullish",
            "WATCH",
            0.8,
            100.0,
            "healthy",
            "{}",
            "{}",
            "[]",
            "[]",
            "[]",
            "{}",
            effective_date,
            signal_key,
        ),
    )


def test_versioned_store_allows_same_history_across_engines_and_scopes_live_queries(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    legacy = V6DailyStore(str(path))
    assert legacy.quick_check().lower() == "ok"

    store = VersionedV6DailyStore(str(path), active_engine_version="engine-a")
    assert store.signal_identity_migrated is True

    with store.connect() as conn:
        _insert_signal(
            conn,
            history_id=1,
            engine="engine-a",
            effective_date="2026-08-11",
            created_at="2026-08-11T01:00:00",
            signal_key="MSFT|2026-08-11|engine-a",
        )
        _insert_signal(
            conn,
            history_id=1,
            engine="engine-b",
            effective_date="2026-08-11",
            created_at="2026-08-11T02:00:00",
            signal_key="MSFT|2026-08-11|engine-b",
        )
        # Insert an older replay record after the live record. MAX(id) must not
        # make it the production latest board item.
        _insert_signal(
            conn,
            history_id=2,
            engine="engine-a",
            effective_date="2026-08-01",
            created_at="2026-08-01T01:00:00",
            signal_key="MSFT|2026-08-01|engine-a",
        )

    assert store.has_analysis_history_version(1) is True
    assert store.counts()["signals"] == 2
    assert len(store.all_signals()) == 2
    board = store.latest_board()
    assert len(board) == 1
    assert board[0]["engine_version"] == "engine-a"
    assert board[0]["effective_trade_date"] == "2026-08-11"

    with store.connect() as conn:
        unique_indexes = []
        for row in conn.execute("PRAGMA index_list(v6_signals)").fetchall():
            if not int(row[2]):
                continue
            cols = [
                str(item[2])
                for item in conn.execute(f"PRAGMA index_info('{row[1]}')").fetchall()
            ]
            unique_indexes.append(cols)
        assert ["analysis_history_id"] not in unique_indexes
        assert ["analysis_history_id", "engine_version"] in unique_indexes
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_final_decision_audit_is_append_only_idempotent_and_tracks_transitions(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    audit = FinalDecisionAuditStore(str(path))

    watch_payload = _payload(decision="WATCH", engine_version="engine-a")
    watch_packets = build_final_decision_packets(
        watch_payload,
        v4_records=[_v4_record(operation="观望")],
    )
    first = audit.persist_packets(
        watch_payload,
        watch_packets,
        report_date="2026-08-11",
        source_engine_version="engine-a",
    )
    again = audit.persist_packets(
        watch_payload,
        watch_packets,
        report_date="2026-08-11",
        source_engine_version="engine-a",
    )
    assert first["inserted"] == 1
    assert again["inserted"] == 0
    assert again["existing"] == 1
    assert again["table_rows"] == 1

    buy_payload = _payload(decision="BUY_SETUP", engine_version="engine-a")
    buy_payload["board"][0]["id"] = 8
    buy_packets = build_final_decision_packets(
        buy_payload,
        v4_records=[_v4_record(operation="买入")],
    )
    second = audit.persist_packets(
        buy_payload,
        buy_packets,
        report_date="2026-08-12",
        source_engine_version="engine-a",
    )
    assert second["inserted"] == 1

    history = audit.history("MSFT", source_engine_version="engine-a")
    assert len(history) == 2
    assert history[0]["verdict"] == "buy_by_plan"
    assert history[0]["worth_buying"] is True
    assert history[0]["execution_authorized"] is True
    assert history[1]["verdict"] == "conditional_buy"
    assert history[1]["execution_authorized"] is False

    transitions = audit.transitions("MSFT", source_engine_version="engine-a")
    assert len(transitions) == 1
    assert transitions[0]["changes"]["verdict"] == {
        "before": "conditional_buy",
        "after": "buy_by_plan",
    }
    assert transitions[0]["changes"]["execution_authorized"] == {
        "before": False,
        "after": True,
    }

    # Same V4 history id can be audited under a different engine version.
    challenger_payload = _payload(decision="WATCH", engine_version="engine-b")
    challenger_payload["board"][0]["id"] = 9
    challenger_packets = build_final_decision_packets(
        challenger_payload,
        v4_records=[_v4_record(operation="观望")],
    )
    third = audit.persist_packets(
        challenger_payload,
        challenger_packets,
        report_date="2026-08-11",
        source_engine_version="engine-b",
    )
    assert third["inserted"] == 1
    with audit.connect() as conn:
        engines = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT source_engine_version FROM v6_final_decisions"
            ).fetchall()
        }
    assert engines == {"engine-a", "engine-b"}
    assert audit.quick_check().lower() == "ok"
