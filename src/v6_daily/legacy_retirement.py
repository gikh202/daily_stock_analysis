from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

from .normalized_accuracy_lab import (
    SHADOW_FORECAST_TABLE,
    SHADOW_OUTCOME_TABLE,
    TRADE_OUTCOME_TABLE,
)


LEGACY_RETIREMENT_SCHEMA_VERSION = "v6-legacy-retirement-readiness-v2"
LEGACY_TABLES = ("v6_signals", "v6_outcomes")
ACTIVE_CONSUMERS = (
    ("daily_business_payload", "normalized_v6_tables"),
    ("accuracy_lab", "normalized_v6_tables"),
    ("live_manifest", "normalized_v6_tables"),
    ("production_read_cutover", "normalized_v6_tables"),
)


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def evaluate_legacy_retirement(
    v6_db_path: str | Path,
    *,
    projection_enabled: bool | None = None,
) -> Dict[str, Any]:
    """Return whether active consumers are independent from legacy V6 facts.

    ``projection_enabled`` is an execution-policy input rather than an inference
    from table presence. Stage 9 callers may omit it and retain the observation-
    window behavior; Stage 10 passes ``False`` explicitly so historical legacy
    tables can remain present without being classified as an active projection.
    """
    with _connect(v6_db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "v6_forecast_runs",
            "v6_decision_runs",
            "v6_execution_plans",
            "v6_forecast_outcomes",
            SHADOW_FORECAST_TABLE,
            SHADOW_OUTCOME_TABLE,
            TRADE_OUTCOME_TABLE,
        }
        missing = sorted(required - tables)

        legacy_fk_dependencies: list[Dict[str, Any]] = []
        for table in (SHADOW_FORECAST_TABLE, SHADOW_OUTCOME_TABLE, TRADE_OUTCOME_TABLE):
            if table not in tables:
                continue
            for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
                target = str(row[2] or "")
                if target in LEGACY_TABLES:
                    legacy_fk_dependencies.append(
                        {
                            "table": table,
                            "target": target,
                            "from": str(row[3] or ""),
                            "to": str(row[4] or ""),
                        }
                    )

        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        legacy_present = [name for name in LEGACY_TABLES if name in tables]
        legacy_counts = {
            name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in legacy_present
        }
        normalized_signals = (
            int(conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0])
            if "v6_forecast_runs" in tables
            else 0
        )
        normalized_outcomes = (
            int(conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0])
            if "v6_forecast_outcomes" in tables
            else 0
        )

    healthy = (
        not missing
        and not legacy_fk_dependencies
        and quick.strip().lower() == "ok"
        and not fk_errors
        and normalized_signals > 0
    )
    legacy_consumer_count = 0 if healthy else len(legacy_fk_dependencies) + len(missing)
    effective_projection_enabled = (
        bool(legacy_present) if projection_enabled is None else bool(projection_enabled)
    )
    return {
        "schema_version": LEGACY_RETIREMENT_SCHEMA_VERSION,
        "status": "retirement_ready" if healthy else "blocked",
        "projection_retirement_ready": healthy,
        "legacy_projection_required_by_active_consumers": not healthy,
        "legacy_consumer_count": legacy_consumer_count,
        "active_consumer_count": len(ACTIVE_CONSUMERS),
        "active_consumers": [
            {"name": name, "source": source} for name, source in ACTIVE_CONSUMERS
        ],
        "legacy_fk_dependencies": legacy_fk_dependencies,
        "missing_normalized_dependencies": missing,
        "normalized_signals": normalized_signals,
        "normalized_outcomes": normalized_outcomes,
        "legacy_tables_present": legacy_present,
        "legacy_table_counts": legacy_counts,
        "legacy_projection_enabled": effective_projection_enabled,
        "legacy_projection_policy": (
            "historical_read_only_explicit_migration_source"
            if projection_enabled is False
            else "temporary_observation_window_only"
        ),
        "quick_check": quick,
        "foreign_key_errors": len(fk_errors),
    }


def assert_legacy_retirement_ready(
    v6_db_path: str | Path,
    *,
    projection_enabled: bool | None = None,
) -> Dict[str, Any]:
    status = evaluate_legacy_retirement(
        v6_db_path,
        projection_enabled=projection_enabled,
    )
    if status.get("projection_retirement_ready") is not True:
        raise RuntimeError(
            "V6 legacy projection retirement is not ready: " + repr(status)
        )
    return status
