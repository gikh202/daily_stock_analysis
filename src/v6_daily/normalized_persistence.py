from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .decision_contracts import (
    DECISION_PACKET_SCHEMA_VERSION,
    DecisionPacket,
    build_assessment,
    build_execution_plan,
)


NORMALIZED_PERSISTENCE_SCHEMA_VERSION = "v6-normalized-persistence-v1"
NORMALIZED_PERSISTENCE_MODE = "legacy_primary_dual_write"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_db(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _horizon_days(key: Any) -> Optional[int]:
    match = re.search(r"(\d+)", str(key or ""))
    if not match:
        return None
    try:
        days = int(match.group(1))
    except ValueError:
        return None
    return days if days > 0 else None


def _packet_from_signal_row(row: Mapping[str, Any]) -> DecisionPacket:
    trade_plan = _json_object(row.get("trade_plan_json"))
    catalysts = tuple(str(item) for item in _json_list(row.get("catalysts_json")) if str(item).strip())
    risks = tuple(str(item) for item in _json_list(row.get("risks_json")) if str(item).strip())
    limitations = tuple(
        str(item) for item in _json_list(row.get("limitations_json")) if str(item).strip()
    )
    diagnostics = _json_object(row.get("diagnostics_json"))
    horizon_forecasts = _json_object(row.get("horizon_forecasts_json"))
    execution = build_execution_plan(row.get("decision"), trade_plan)
    assessment = build_assessment(
        row.get("decision"),
        execution,
        catalysts=catalysts,
        risks=risks,
    )
    return DecisionPacket(
        symbol=str(row.get("code") or "").strip().upper(),
        instrument_type=str(row.get("instrument_type") or "STOCK").strip().upper(),
        effective_trade_date=str(row.get("effective_trade_date") or "").strip() or None,
        direction=str(row.get("direction") or "neutral").strip().lower(),
        forecast_score=_finite(row.get("forecast_score")),
        horizon_forecasts=horizon_forecasts,
        quality_score=_finite(row.get("quality_score")),
        opportunity_score=_finite(row.get("opportunity_score")),
        risk_score=_finite(row.get("risk_score")),
        evidence_coverage=float(_finite(row.get("evidence_coverage")) or 0.0),
        assessment=assessment,
        execution=execution,
        catalysts=catalysts,
        risks=risks,
        limitations=limitations,
        engine_version=str(row.get("engine_version") or diagnostics.get("engine_version") or "unknown"),
        feature_adapter_version=(
            str(diagnostics.get("feature_adapter_version"))
            if diagnostics.get("feature_adapter_version") is not None
            else None
        ),
    )


class NormalizedV6Persistence:
    """Shadow normalized persistence for the production V6 domain model.

    The existing ``v6_signals`` and ``v6_outcomes`` tables remain the production
    read source during this phase. This store mirrors their immutable facts into
    smaller domain tables in one fail-closed transaction. A later phase may cut
    reads over only after parity is proven in production and historical replay.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS v6_run_manifests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version TEXT NOT NULL,
                    run_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    run_mode TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    payload_version TEXT,
                    source_snapshot_hash TEXT NOT NULL,
                    source_signal_count INTEGER NOT NULL,
                    source_outcome_count INTEGER NOT NULL,
                    board_symbols_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v6_forecast_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_signal_id INTEGER NOT NULL UNIQUE,
                    run_manifest_id INTEGER,
                    analysis_history_id INTEGER NOT NULL,
                    query_id TEXT,
                    symbol TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    effective_trade_date TEXT,
                    analysis_created_at TEXT NOT NULL,
                    v6_created_at TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    forecast_score REAL,
                    baseline_price REAL NOT NULL,
                    evidence_coverage REAL NOT NULL,
                    market_regime TEXT,
                    market_breadth TEXT,
                    llm_health TEXT NOT NULL,
                    feature_adapter_version TEXT,
                    features_json TEXT NOT NULL,
                    context_features_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    FOREIGN KEY(run_manifest_id) REFERENCES v6_run_manifests(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS v6_horizon_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    forecast_run_id INTEGER NOT NULL,
                    horizon_key TEXT NOT NULL,
                    horizon_days INTEGER,
                    direction TEXT,
                    score REAL,
                    expected_return_pct REAL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(forecast_run_id, horizon_key),
                    FOREIGN KEY(forecast_run_id) REFERENCES v6_forecast_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS v6_decision_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_signal_id INTEGER NOT NULL UNIQUE,
                    forecast_run_id INTEGER NOT NULL UNIQUE,
                    run_manifest_id INTEGER,
                    decision_schema_version TEXT NOT NULL,
                    assessment_scope TEXT NOT NULL,
                    assessment_is_final INTEGER NOT NULL,
                    deterministic_decision TEXT NOT NULL,
                    assessment_verdict TEXT NOT NULL,
                    worth_buying INTEGER,
                    quality_score REAL,
                    opportunity_score REAL,
                    risk_score REAL,
                    evidence_coverage REAL NOT NULL,
                    execution_status TEXT NOT NULL,
                    execution_actionable INTEGER NOT NULL,
                    decision_packet_json TEXT NOT NULL,
                    FOREIGN KEY(forecast_run_id) REFERENCES v6_forecast_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(run_manifest_id) REFERENCES v6_run_manifests(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS v6_execution_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_run_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entry_low REAL,
                    entry_high REAL,
                    stop_loss REAL,
                    targets_json TEXT NOT NULL,
                    max_position_pct REAL NOT NULL,
                    risk_reward REAL,
                    confirmations_json TEXT NOT NULL,
                    invalidations_json TEXT NOT NULL,
                    has_active_plan INTEGER NOT NULL,
                    actionable INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    FOREIGN KEY(decision_run_id) REFERENCES v6_decision_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS v6_forecast_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_outcome_id INTEGER NOT NULL UNIQUE,
                    forecast_run_id INTEGER NOT NULL,
                    source_signal_id INTEGER NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    end_trade_date TEXT NOT NULL,
                    start_price REAL NOT NULL,
                    end_price REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    directional_hit INTEGER,
                    forecast_score REAL,
                    direction_used TEXT,
                    benchmark_spy_return_pct REAL,
                    benchmark_qqq_return_pct REAL,
                    excess_vs_spy_pct REAL,
                    excess_vs_qqq_pct REAL,
                    UNIQUE(forecast_run_id, horizon_days),
                    FOREIGN KEY(forecast_run_id) REFERENCES v6_forecast_runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_v6_run_manifests_engine_date
                    ON v6_run_manifests(engine_version, report_date, id);
                CREATE INDEX IF NOT EXISTS ix_v6_forecast_runs_symbol_date
                    ON v6_forecast_runs(symbol, effective_trade_date, id);
                CREATE INDEX IF NOT EXISTS ix_v6_forecast_runs_engine_date
                    ON v6_forecast_runs(engine_version, effective_trade_date, id);
                CREATE INDEX IF NOT EXISTS ix_v6_horizon_forecasts_days
                    ON v6_horizon_forecasts(horizon_days, forecast_run_id);
                CREATE INDEX IF NOT EXISTS ix_v6_decision_runs_verdict
                    ON v6_decision_runs(assessment_verdict, id);
                CREATE INDEX IF NOT EXISTS ix_v6_forecast_outcomes_horizon
                    ON v6_forecast_outcomes(horizon_days, evaluated_at, id);
                """
            )

    def quick_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    @staticmethod
    def _board_signal_ids(payload: Mapping[str, Any]) -> set[int]:
        result: set[int] = set()
        for item in payload.get("board") or []:
            if not isinstance(item, Mapping):
                continue
            signal_id = _int_or_none(item.get("id"))
            if signal_id is not None:
                result.add(signal_id)
        return result

    @staticmethod
    def _board_symbols(payload: Mapping[str, Any]) -> list[str]:
        result: list[str] = []
        for item in payload.get("board") or []:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code") or "").strip().upper()
            if code and code not in result:
                result.append(code)
        return result

    def persist_snapshot(
        self,
        payload: Mapping[str, Any],
        *,
        source_engine_version: str,
        report_date: str,
        run_mode: str = "LIVE",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        engine_version = str(source_engine_version or "").strip()
        if not engine_version:
            raise ValueError("source_engine_version is required")
        normalized_mode = str(run_mode or "LIVE").strip().upper()
        if normalized_mode not in {"LIVE", "REPLAY", "SHADOW"}:
            raise ValueError(f"unsupported run_mode: {run_mode!r}")
        report_day = str(report_date or "").strip()[:10]
        if not report_day:
            raise ValueError("report_date is required")

        board_signal_ids = self._board_signal_ids(payload)
        board_symbols = self._board_symbols(payload)
        manifest_inserted = False

        with self.connect() as conn:
            signals = conn.execute(
                "SELECT * FROM v6_signals WHERE engine_version=? ORDER BY id ASC",
                (engine_version,),
            ).fetchall()
            outcomes = conn.execute(
                """
                SELECT o.*
                FROM v6_outcomes o
                JOIN v6_signals s ON s.id=o.signal_id
                WHERE s.engine_version=?
                ORDER BY o.id ASC
                """,
                (engine_version,),
            ).fetchall()

            source_snapshot_hash = _stable_hash(
                {
                    "engine_version": engine_version,
                    "signals": [
                        [
                            int(row["id"]),
                            int(row["analysis_history_id"]),
                            str(row["effective_trade_date"] or ""),
                            str(row["analysis_created_at"] or ""),
                            str(row["direction"] or ""),
                            row["forecast_score"],
                            str(row["decision"] or ""),
                        ]
                        for row in signals
                    ],
                    "outcomes": [
                        [int(row["id"]), int(row["signal_id"]), int(row["horizon_days"]), row["return_pct"]]
                        for row in outcomes
                    ],
                }
            )
            run_key = _stable_hash(
                {
                    "schema_version": NORMALIZED_PERSISTENCE_SCHEMA_VERSION,
                    "run_mode": normalized_mode,
                    "engine_version": engine_version,
                    "report_date": report_day,
                    "payload_version": payload.get("version"),
                    "source_snapshot_hash": source_snapshot_hash,
                }
            )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO v6_run_manifests(
                    schema_version, run_key, created_at, report_date, run_mode,
                    engine_version, payload_version, source_snapshot_hash,
                    source_signal_count, source_outcome_count,
                    board_symbols_json, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    NORMALIZED_PERSISTENCE_SCHEMA_VERSION,
                    run_key,
                    _utc_now(),
                    report_day,
                    normalized_mode,
                    engine_version,
                    str(payload.get("version") or ""),
                    source_snapshot_hash,
                    len(signals),
                    len(outcomes),
                    _json(board_symbols),
                    _json(dict(metadata or {})),
                ),
            )
            manifest_inserted = cursor.rowcount > 0
            manifest_row = conn.execute(
                "SELECT id FROM v6_run_manifests WHERE run_key=?",
                (run_key,),
            ).fetchone()
            if manifest_row is None:
                raise RuntimeError("normalized run manifest was not persisted")
            manifest_id = int(manifest_row["id"])

            expected_horizons = 0
            for signal_row in signals:
                source_signal_id = int(signal_row["id"])
                packet = _packet_from_signal_row(signal_row)
                packet_dict = packet.to_dict()
                diagnostics = _json_object(signal_row["diagnostics_json"])
                run_manifest_id = manifest_id if source_signal_id in board_signal_ids else None

                conn.execute(
                    """
                    INSERT INTO v6_forecast_runs(
                        source_signal_id, run_manifest_id, analysis_history_id,
                        query_id, symbol, instrument_type, effective_trade_date,
                        analysis_created_at, v6_created_at, engine_version,
                        direction, forecast_score, baseline_price, evidence_coverage,
                        market_regime, market_breadth, llm_health,
                        feature_adapter_version, features_json,
                        context_features_json, diagnostics_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_signal_id) DO UPDATE SET
                        run_manifest_id=COALESCE(v6_forecast_runs.run_manifest_id, excluded.run_manifest_id)
                    """,
                    (
                        source_signal_id,
                        run_manifest_id,
                        int(signal_row["analysis_history_id"]),
                        str(signal_row["query_id"] or ""),
                        packet.symbol,
                        packet.instrument_type,
                        packet.effective_trade_date,
                        str(signal_row["analysis_created_at"] or ""),
                        str(signal_row["v6_created_at"] or ""),
                        engine_version,
                        packet.direction,
                        packet.forecast_score,
                        float(signal_row["baseline_price"]),
                        float(packet.evidence_coverage),
                        str(signal_row["market_regime"] or ""),
                        str(signal_row["market_breadth"] or ""),
                        str(signal_row["llm_health"] or "unknown"),
                        (
                            str(diagnostics.get("feature_adapter_version"))
                            if diagnostics.get("feature_adapter_version") is not None
                            else None
                        ),
                        str(signal_row["features_json"] or "{}"),
                        str(signal_row["context_features_json"] or "{}"),
                        str(signal_row["diagnostics_json"] or "{}"),
                    ),
                )
                forecast_row = conn.execute(
                    "SELECT id FROM v6_forecast_runs WHERE source_signal_id=?",
                    (source_signal_id,),
                ).fetchone()
                if forecast_row is None:
                    raise RuntimeError(f"forecast run missing for source signal {source_signal_id}")
                forecast_run_id = int(forecast_row["id"])

                for horizon_key, block_value in packet.horizon_forecasts.items():
                    block = dict(block_value) if isinstance(block_value, Mapping) else {}
                    expected_horizons += 1
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO v6_horizon_forecasts(
                            forecast_run_id, horizon_key, horizon_days,
                            direction, score, expected_return_pct, payload_json
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            forecast_run_id,
                            str(horizon_key),
                            _horizon_days(horizon_key),
                            str(block.get("direction") or ""),
                            _finite(block.get("score")),
                            _finite(block.get("expected_return_pct") or block.get("target_return_pct")),
                            _json(block),
                        ),
                    )

                conn.execute(
                    """
                    INSERT INTO v6_decision_runs(
                        source_signal_id, forecast_run_id, run_manifest_id,
                        decision_schema_version, assessment_scope, assessment_is_final,
                        deterministic_decision, assessment_verdict, worth_buying,
                        quality_score, opportunity_score, risk_score, evidence_coverage,
                        execution_status, execution_actionable, decision_packet_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_signal_id) DO UPDATE SET
                        run_manifest_id=COALESCE(v6_decision_runs.run_manifest_id, excluded.run_manifest_id)
                    """,
                    (
                        source_signal_id,
                        forecast_run_id,
                        run_manifest_id,
                        DECISION_PACKET_SCHEMA_VERSION,
                        packet.assessment.scope,
                        int(bool(packet.assessment.is_final)),
                        str(signal_row["decision"] or ""),
                        packet.assessment.verdict.value,
                        _bool_db(packet.assessment.worth_buying),
                        packet.quality_score,
                        packet.opportunity_score,
                        packet.risk_score,
                        float(packet.evidence_coverage),
                        packet.execution.status.value,
                        int(bool(packet.execution.actionable)),
                        _json(packet_dict),
                    ),
                )
                decision_row = conn.execute(
                    "SELECT id FROM v6_decision_runs WHERE source_signal_id=?",
                    (source_signal_id,),
                ).fetchone()
                if decision_row is None:
                    raise RuntimeError(f"decision run missing for source signal {source_signal_id}")
                decision_run_id = int(decision_row["id"])
                execution_dict = packet.execution.to_dict()
                entry_zone = packet.execution.entry_zone
                conn.execute(
                    """
                    INSERT OR IGNORE INTO v6_execution_plans(
                        decision_run_id, status, action, entry_low, entry_high,
                        stop_loss, targets_json, max_position_pct, risk_reward,
                        confirmations_json, invalidations_json,
                        has_active_plan, actionable, plan_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        decision_run_id,
                        packet.execution.status.value,
                        packet.execution.action,
                        entry_zone[0] if entry_zone else None,
                        entry_zone[1] if entry_zone else None,
                        packet.execution.stop_loss,
                        _json(list(packet.execution.targets)),
                        float(packet.execution.max_position_pct),
                        packet.execution.risk_reward,
                        _json(list(packet.execution.confirmations)),
                        _json(list(packet.execution.invalidations)),
                        int(bool(packet.execution.has_active_plan)),
                        int(bool(packet.execution.actionable)),
                        _json(execution_dict),
                    ),
                )

            for outcome_row in outcomes:
                source_signal_id = int(outcome_row["signal_id"])
                forecast_row = conn.execute(
                    "SELECT id FROM v6_forecast_runs WHERE source_signal_id=?",
                    (source_signal_id,),
                ).fetchone()
                if forecast_row is None:
                    raise RuntimeError(
                        f"cannot normalize outcome without forecast run for source signal {source_signal_id}"
                    )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO v6_forecast_outcomes(
                        source_outcome_id, forecast_run_id, source_signal_id,
                        horizon_days, evaluated_at, end_trade_date,
                        start_price, end_price, return_pct, mfe_pct, mae_pct,
                        directional_hit, forecast_score, direction_used,
                        benchmark_spy_return_pct, benchmark_qqq_return_pct,
                        excess_vs_spy_pct, excess_vs_qqq_pct
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(outcome_row["id"]),
                        int(forecast_row["id"]),
                        source_signal_id,
                        int(outcome_row["horizon_days"]),
                        str(outcome_row["evaluated_at"] or ""),
                        str(outcome_row["end_trade_date"] or ""),
                        float(outcome_row["start_price"]),
                        float(outcome_row["end_price"]),
                        float(outcome_row["return_pct"]),
                        _finite(outcome_row["mfe_pct"]),
                        _finite(outcome_row["mae_pct"]),
                        _int_or_none(outcome_row["directional_hit"]),
                        _finite(outcome_row["forecast_score"]),
                        str(outcome_row["direction_used"] or ""),
                        _finite(outcome_row["benchmark_spy_return_pct"]),
                        _finite(outcome_row["benchmark_qqq_return_pct"]),
                        _finite(outcome_row["excess_vs_spy_pct"]),
                        _finite(outcome_row["excess_vs_qqq_pct"]),
                    ),
                )

            normalized_forecasts = int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_forecast_runs WHERE engine_version=?",
                    (engine_version,),
                ).fetchone()[0]
            )
            normalized_decisions = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_decision_runs d
                    JOIN v6_forecast_runs f ON f.id=d.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (engine_version,),
                ).fetchone()[0]
            )
            normalized_plans = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_execution_plans p
                    JOIN v6_decision_runs d ON d.id=p.decision_run_id
                    JOIN v6_forecast_runs f ON f.id=d.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (engine_version,),
                ).fetchone()[0]
            )
            normalized_horizons = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_horizon_forecasts h
                    JOIN v6_forecast_runs f ON f.id=h.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (engine_version,),
                ).fetchone()[0]
            )
            normalized_outcomes = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_forecast_outcomes o
                    JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (engine_version,),
                ).fetchone()[0]
            )

            mismatches = []
            if normalized_forecasts != len(signals):
                mismatches.append(f"forecast_runs={normalized_forecasts} source_signals={len(signals)}")
            if normalized_decisions != len(signals):
                mismatches.append(f"decision_runs={normalized_decisions} source_signals={len(signals)}")
            if normalized_plans != len(signals):
                mismatches.append(f"execution_plans={normalized_plans} source_signals={len(signals)}")
            if normalized_horizons != expected_horizons:
                mismatches.append(
                    f"horizon_forecasts={normalized_horizons} expected_horizons={expected_horizons}"
                )
            if normalized_outcomes != len(outcomes):
                mismatches.append(
                    f"forecast_outcomes={normalized_outcomes} source_outcomes={len(outcomes)}"
                )
            if mismatches:
                raise RuntimeError("normalized persistence parity failed: " + "; ".join(mismatches))

            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise RuntimeError(
                    f"normalized persistence foreign_key_check failed: {foreign_key_errors[:3]}"
                )

        quick = self.quick_check()
        if quick.strip().lower() != "ok":
            raise RuntimeError(f"normalized persistence quick_check failed: {quick}")
        return {
            "schema_version": NORMALIZED_PERSISTENCE_SCHEMA_VERSION,
            "mode": NORMALIZED_PERSISTENCE_MODE,
            "run_mode": normalized_mode,
            "engine_version": engine_version,
            "manifest_id": manifest_id,
            "manifest_inserted": manifest_inserted,
            "source_snapshot_hash": source_snapshot_hash,
            "source_signals": len(signals),
            "source_outcomes": len(outcomes),
            "forecast_runs": normalized_forecasts,
            "horizon_forecasts": normalized_horizons,
            "decision_runs": normalized_decisions,
            "execution_plans": normalized_plans,
            "forecast_outcomes": normalized_outcomes,
            "parity": "exact",
            "quick_check": quick,
        }
