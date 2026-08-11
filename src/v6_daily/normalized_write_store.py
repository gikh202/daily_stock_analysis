from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import V6Signal
from .normalized_persistence import NormalizedV6Persistence, _horizon_days
from .store import _finite, _json, _parse_date, _utc_now


NORMALIZED_ONLY_WRITE_SCHEMA_VERSION = "v6-normalized-write-primary-v2"
NORMALIZED_ONLY_WRITE_MODE = "normalized_only_no_legacy_projection"


def _bool_db(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0


class NormalizedOnlyV6WriteStore:
    """Production V6 writer with normalized facts as the only write destination.

    Stage 10 deliberately does not create, migrate, bootstrap, project into, or
    allocate identities from ``v6_signals`` / ``v6_outcomes``. Existing legacy
    tables may remain in the database as historical read-only migration sources,
    but this store has no SQL dependency on them.
    """

    def __init__(self, path: str, *, active_engine_version: str) -> None:
        engine = str(active_engine_version or "").strip()
        if not engine:
            raise ValueError("active_engine_version is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.active_engine_version = engine
        # Reuse only the normalized schema initializer. persist_snapshot() is not
        # called here because that older migration API reads legacy fact tables.
        self._normalized_schema = NormalizedV6Persistence(str(self.path))
        self.signal_identity_migrated = False
        self.bootstrap_summary: Dict[str, Any] = {
            "performed": False,
            "automatic": False,
            "reason": "automatic_legacy_bootstrap_disabled",
        }
        self._write_counters: Dict[str, int] = {
            "canonical_signals_inserted": 0,
            "canonical_outcomes_inserted": 0,
            "legacy_signal_projection_writes": 0,
            "legacy_outcome_projection_writes": 0,
        }

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _next_normalized_identity(
        conn: sqlite3.Connection,
        *,
        normalized_table: str,
        normalized_column: str,
    ) -> int:
        """Allocate compatibility identity only from normalized canonical facts."""
        current = int(
            conn.execute(
                f"SELECT COALESCE(MAX({normalized_column}), 0) FROM {normalized_table}"
            ).fetchone()[0]
        )
        return current + 1

    def has_analysis_history_version(self, history_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM v6_forecast_runs "
                "WHERE analysis_history_id=? AND engine_version=? LIMIT 1",
                (int(history_id), self.active_engine_version),
            ).fetchone()
        return row is not None

    def has_signal_key(self, code: str, effective_trade_date: Any, engine_version: str) -> bool:
        effective = _parse_date(effective_trade_date)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM v6_forecast_runs
                WHERE symbol=? AND engine_version=?
                  AND COALESCE(effective_trade_date, '')=COALESCE(?, '')
                LIMIT 1
                """,
                (str(code or "").strip().upper(), str(engine_version or "").strip(), effective),
            ).fetchone()
        return row is not None

    def save_signal(self, signal: V6Signal, *, engine_version: str) -> bool:
        engine = str(engine_version or "").strip()
        if engine != self.active_engine_version:
            raise ValueError(
                "NormalizedOnlyV6WriteStore only accepts the active engine version: "
                f"{engine!r} != {self.active_engine_version!r}"
            )
        effective = signal.effective_trade_date or _parse_date(signal.analysis_created_at)
        packet = signal.to_decision_packet()
        packet_dict = packet.to_dict()
        execution_dict = packet.execution.to_dict()
        created_at = _utc_now()

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT source_signal_id
                FROM v6_forecast_runs
                WHERE engine_version=? AND (
                    analysis_history_id=? OR (
                        symbol=? AND COALESCE(effective_trade_date, '')=COALESCE(?, '')
                    )
                )
                ORDER BY id DESC LIMIT 1
                """,
                (
                    engine,
                    int(signal.analysis_history_id),
                    str(signal.code or "").strip().upper(),
                    effective,
                ),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return False

            source_signal_id = self._next_normalized_identity(
                conn,
                normalized_table="v6_forecast_runs",
                normalized_column="source_signal_id",
            )
            diagnostics = dict(signal.diagnostics or {})
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
                """,
                (
                    source_signal_id,
                    None,
                    int(signal.analysis_history_id),
                    signal.query_id,
                    str(signal.code or "").strip().upper(),
                    str(signal.instrument_type or "STOCK").strip().upper(),
                    effective,
                    signal.analysis_created_at,
                    created_at,
                    engine,
                    signal.direction,
                    _finite(signal.forecast_score),
                    float(signal.baseline_price),
                    float(signal.evidence_coverage),
                    signal.market_regime,
                    signal.market_breadth,
                    signal.llm_health,
                    (
                        str(diagnostics.get("feature_adapter_version"))
                        if diagnostics.get("feature_adapter_version") is not None
                        else None
                    ),
                    _json(signal.features),
                    _json(signal.context_features),
                    _json(diagnostics),
                ),
            )
            forecast_run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            for horizon_key, block_value in signal.horizon_forecasts.items():
                block = dict(block_value) if isinstance(block_value, dict) else {}
                conn.execute(
                    """
                    INSERT INTO v6_horizon_forecasts(
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
                        _finite(
                            block.get("expected_return_pct")
                            if block.get("expected_return_pct") is not None
                            else block.get("target_return_pct")
                        ),
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
                """,
                (
                    source_signal_id,
                    forecast_run_id,
                    None,
                    str(packet_dict.get("schema_version") or "decision-packet-v1"),
                    packet.assessment.scope,
                    int(bool(packet.assessment.is_final)),
                    signal.decision,
                    packet.assessment.verdict.value,
                    _bool_db(packet.assessment.worth_buying),
                    _finite(signal.quality_score),
                    _finite(signal.opportunity_score),
                    _finite(signal.risk_score),
                    float(signal.evidence_coverage),
                    packet.execution.status.value,
                    int(bool(packet.execution.actionable)),
                    _json(packet_dict),
                ),
            )
            decision_run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            entry_zone = packet.execution.entry_zone
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
            conn.commit()

        self._write_counters["canonical_signals_inserted"] += 1
        return True

    def all_signals(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.source_signal_id AS id, f.symbol AS code,
                       f.effective_trade_date, f.analysis_created_at,
                       f.baseline_price, f.direction, f.forecast_score,
                       d.decision_packet_json
                FROM v6_forecast_runs f
                JOIN v6_decision_runs d ON d.forecast_run_id=f.id
                WHERE f.engine_version=?
                ORDER BY f.analysis_created_at ASC, f.source_signal_id ASC
                """,
                (self.active_engine_version,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                packet = json.loads(str(item.pop("decision_packet_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                packet = {}
            forecast = packet.get("forecast") if isinstance(packet, dict) else {}
            horizons = forecast.get("horizons") if isinstance(forecast, dict) else {}
            item["horizon_forecasts_json"] = _json(
                horizons if isinstance(horizons, dict) else {}
            )
            result.append(item)
        return result

    def evaluated_horizons(self, signal_id: int) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT o.horizon_days
                FROM v6_forecast_outcomes o
                JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                WHERE f.source_signal_id=? AND f.engine_version=?
                """,
                (int(signal_id), self.active_engine_version),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def save_outcome(
        self,
        *,
        signal_id: int,
        horizon_days: int,
        end_trade_date: str,
        start_price: float,
        end_price: float,
        max_high: Optional[float],
        min_low: Optional[float],
        direction: str,
        neutral_band_pct: float = 2.0,
        forecast_score: Optional[float] = None,
        benchmark_spy_return_pct: Optional[float] = None,
        benchmark_qqq_return_pct: Optional[float] = None,
    ) -> bool:
        return_pct = (end_price / start_price - 1.0) * 100.0
        mfe = None if max_high is None else (max_high / start_price - 1.0) * 100.0
        mae = None if min_low is None else (min_low / start_price - 1.0) * 100.0
        normalized_direction = str(direction or "").strip().lower()
        if normalized_direction == "bullish":
            hit = int(return_pct > 0.0)
        elif normalized_direction == "bearish":
            hit = int(return_pct < 0.0)
        elif normalized_direction == "neutral":
            hit = int(abs(return_pct) <= abs(float(neutral_band_pct)))
        else:
            hit = None
        excess_spy = (
            None
            if benchmark_spy_return_pct is None
            else return_pct - float(benchmark_spy_return_pct)
        )
        excess_qqq = (
            None
            if benchmark_qqq_return_pct is None
            else return_pct - float(benchmark_qqq_return_pct)
        )

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            forecast = conn.execute(
                "SELECT id FROM v6_forecast_runs WHERE source_signal_id=? AND engine_version=?",
                (int(signal_id), self.active_engine_version),
            ).fetchone()
            if forecast is None:
                raise RuntimeError(
                    f"canonical forecast missing for outcome signal_id={signal_id}"
                )
            forecast_run_id = int(forecast["id"])
            existing = conn.execute(
                "SELECT 1 FROM v6_forecast_outcomes WHERE forecast_run_id=? AND horizon_days=?",
                (forecast_run_id, int(horizon_days)),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return False

            source_outcome_id = self._next_normalized_identity(
                conn,
                normalized_table="v6_forecast_outcomes",
                normalized_column="source_outcome_id",
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
                    source_outcome_id,
                    forecast_run_id,
                    int(signal_id),
                    int(horizon_days),
                    _utc_now(),
                    end_trade_date,
                    float(start_price),
                    float(end_price),
                    round(return_pct, 6),
                    None if mfe is None else round(mfe, 6),
                    None if mae is None else round(mae, 6),
                    hit,
                    _finite(forecast_score),
                    normalized_direction,
                    _finite(benchmark_spy_return_pct),
                    _finite(benchmark_qqq_return_pct),
                    None if excess_spy is None else round(excess_spy, 6),
                    None if excess_qqq is None else round(excess_qqq, 6),
                ),
            )
            conn.commit()

        self._write_counters["canonical_outcomes_inserted"] += 1
        return True

    def quick_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def write_status(self) -> Dict[str, Any]:
        with self.connect() as conn:
            canonical_signals = int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_forecast_runs WHERE engine_version=?",
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
            decision_runs = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_decision_runs d
                    JOIN v6_forecast_runs f ON f.id=d.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
            execution_plans = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_execution_plans p
                    JOIN v6_decision_runs d ON d.id=p.decision_run_id
                    JOIN v6_forecast_runs f ON f.id=d.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
            canonical_outcomes = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_forecast_outcomes o
                    JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])

        parity_exact = (
            canonical_signals == decision_runs == execution_plans
            and not fk_errors
            and quick.strip().lower() == "ok"
        )
        return {
            "schema_version": NORMALIZED_ONLY_WRITE_SCHEMA_VERSION,
            "mode": NORMALIZED_ONLY_WRITE_MODE,
            "canonical_source": "normalized_v6_tables",
            "identity_source": "normalized_sequence_only",
            "legacy_role": "historical_read_only_explicit_migration_source",
            "legacy_projection_enabled": False,
            "legacy_projection_writes": 0,
            "legacy_signal_projection_writes": 0,
            "legacy_outcome_projection_writes": 0,
            "automatic_legacy_bootstrap": False,
            "engine_version": self.active_engine_version,
            "parity": "exact" if parity_exact else "drift",
            "quick_check": quick,
            "foreign_key_errors": len(fk_errors),
            "canonical_signals": canonical_signals,
            "decision_runs": decision_runs,
            "execution_plans": execution_plans,
            "canonical_outcomes": canonical_outcomes,
            "bootstrap": dict(self.bootstrap_summary),
            "run_writes": dict(self._write_counters),
        }
