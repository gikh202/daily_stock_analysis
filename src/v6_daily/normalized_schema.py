from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


NORMALIZED_SCHEMA_REGISTRY_VERSION = "v6-normalized-schema-registry-v1"
NORMALIZED_CORE_SCHEMA_VERSION = "v6-normalized-core-v1"
CORE_MIGRATION_ID = "0001-normalized-core"

_CORE_DDL = """
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
""".strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_normalized_schema(path: str | Path) -> Dict[str, Any]:
    """Apply the registered normalized-core migrations idempotently.

    Existing Stage 5-10 databases are adopted without rebuilding tables: the
    canonical DDL is re-applied with IF NOT EXISTS and then recorded in the
    registry. A checksum mismatch for an already-recorded migration fails closed.
    """
    ddl_checksum = _checksum(_CORE_DDL)
    applied_now = False
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS v6_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                component TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        row = conn.execute(
            "SELECT checksum, schema_version FROM v6_schema_migrations WHERE migration_id=?",
            (CORE_MIGRATION_ID,),
        ).fetchone()
        if row is not None and str(row["checksum"]) != ddl_checksum:
            raise RuntimeError(
                "normalized schema migration checksum mismatch for "
                f"{CORE_MIGRATION_ID}: {row['checksum']} != {ddl_checksum}"
            )
        conn.executescript(_CORE_DDL)
        if row is None:
            conn.execute(
                """
                INSERT INTO v6_schema_migrations(
                    migration_id, component, schema_version, checksum, applied_at
                ) VALUES (?,?,?,?,?)
                """,
                (
                    CORE_MIGRATION_ID,
                    "normalized_core",
                    NORMALIZED_CORE_SCHEMA_VERSION,
                    ddl_checksum,
                    _utc_now(),
                ),
            )
            applied_now = True

        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        tables = {
            str(item[0])
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "v6_run_manifests",
            "v6_forecast_runs",
            "v6_horizon_forecasts",
            "v6_decision_runs",
            "v6_execution_plans",
            "v6_forecast_outcomes",
        }
        missing = sorted(required - tables)
        rows = conn.execute(
            """
            SELECT migration_id, component, schema_version, checksum, applied_at
            FROM v6_schema_migrations ORDER BY migration_id
            """
        ).fetchall()

    current = quick.strip().lower() == "ok" and not fk_errors and not missing
    if not current:
        raise RuntimeError(
            "normalized schema registry is not current: "
            f"quick={quick!r} fk={len(fk_errors)} missing={missing}"
        )
    return {
        "registry_version": NORMALIZED_SCHEMA_REGISTRY_VERSION,
        "core_schema_version": NORMALIZED_CORE_SCHEMA_VERSION,
        "status": "current",
        "pending_migrations": [],
        "applied_now": applied_now,
        "migration_count": len(rows),
        "migrations": [dict(item) for item in rows],
        "quick_check": quick,
        "foreign_key_errors": 0,
        "missing_tables": [],
    }


def normalized_schema_status(path: str | Path) -> Dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return {
            "registry_version": NORMALIZED_SCHEMA_REGISTRY_VERSION,
            "core_schema_version": NORMALIZED_CORE_SCHEMA_VERSION,
            "status": "missing_database",
            "pending_migrations": [CORE_MIGRATION_ID],
            "migration_count": 0,
            "migrations": [],
        }
    with _connect(target) as conn:
        tables = {
            str(item[0])
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "v6_schema_migrations" not in tables:
            return {
                "registry_version": NORMALIZED_SCHEMA_REGISTRY_VERSION,
                "core_schema_version": NORMALIZED_CORE_SCHEMA_VERSION,
                "status": "unregistered",
                "pending_migrations": [CORE_MIGRATION_ID],
                "migration_count": 0,
                "migrations": [],
            }
        rows = conn.execute(
            "SELECT migration_id, component, schema_version, checksum, applied_at FROM v6_schema_migrations ORDER BY migration_id"
        ).fetchall()
        applied = {str(row["migration_id"]) for row in rows}
        pending = [] if CORE_MIGRATION_ID in applied else [CORE_MIGRATION_ID]
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "registry_version": NORMALIZED_SCHEMA_REGISTRY_VERSION,
        "core_schema_version": NORMALIZED_CORE_SCHEMA_VERSION,
        "status": "current" if not pending and quick.strip().lower() == "ok" and not fk_errors else "pending",
        "pending_migrations": pending,
        "migration_count": len(rows),
        "migrations": [dict(item) for item in rows],
        "quick_check": quick,
        "foreign_key_errors": len(fk_errors),
    }
