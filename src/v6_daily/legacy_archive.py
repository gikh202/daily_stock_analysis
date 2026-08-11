from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .normalized_persistence import NormalizedV6Persistence


LEGACY_ARCHIVE_SCHEMA_VERSION = "v6-legacy-archive-v1"
LEGACY_MIGRATION_SCHEMA_VERSION = "v6-explicit-legacy-migration-v1"
LEGACY_FACT_TABLES = ("v6_signals", "v6_outcomes")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _connect_ro(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def inspect_legacy_facts(v6_db_path: str | Path) -> Dict[str, Any]:
    with _connect_ro(v6_db_path) as conn:
        present = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        result: Dict[str, Any] = {}
        for table in LEGACY_FACT_TABLES:
            if table not in present:
                result[table] = {
                    "present": False,
                    "columns": [],
                    "rows": 0,
                    "sha256": None,
                }
                continue
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY id ASC")
            columns = [str(item[0]) for item in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchall()]
            result[table] = {
                "present": True,
                "columns": columns,
                "rows": len(rows),
                "sha256": _hash(rows),
            }
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "database": str(v6_db_path),
        "tables": result,
        "quick_check": quick,
        "foreign_key_errors": len(fk_errors),
    }


def export_legacy_archive(
    v6_db_path: str | Path,
    output_path: str | Path,
) -> Dict[str, Any]:
    """Export legacy V6 fact rows without mutating the source database."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect_ro(v6_db_path) as conn:
        present = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        tables: Dict[str, Any] = {}
        for table in LEGACY_FACT_TABLES:
            if table not in present:
                tables[table] = {
                    "present": False,
                    "columns": [],
                    "rows": [],
                }
                continue
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY id ASC")
            columns = [str(item[0]) for item in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchall()]
            tables[table] = {
                "present": True,
                "columns": columns,
                "rows": rows,
            }
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]

    payload = {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "source_database": str(v6_db_path),
        "source_quick_check": quick,
        "source_foreign_key_errors": len(fk_errors),
        "tables": tables,
    }
    payload["content_sha256"] = _hash(tables)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "status": "exported",
        "output": str(target),
        "content_sha256": payload["content_sha256"],
        "legacy_signal_rows": len((tables.get("v6_signals") or {}).get("rows") or []),
        "legacy_outcome_rows": len((tables.get("v6_outcomes") or {}).get("rows") or []),
        "source_quick_check": quick,
        "source_foreign_key_errors": len(fk_errors),
    }


def plan_legacy_migration(
    v6_db_path: str | Path,
    *,
    engine_version: str,
) -> Dict[str, Any]:
    engine = str(engine_version or "").strip()
    if not engine:
        raise ValueError("engine_version is required")
    with _connect_ro(v6_db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        legacy_signals = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_signals WHERE engine_version=?",
                    (engine,),
                ).fetchone()[0]
            )
            if "v6_signals" in tables
            else 0
        )
        legacy_outcomes = (
            int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_outcomes o
                    JOIN v6_signals s ON s.id=o.signal_id
                    WHERE s.engine_version=?
                    """,
                    (engine,),
                ).fetchone()[0]
            )
            if {"v6_signals", "v6_outcomes"}.issubset(tables)
            else 0
        )
        normalized_signals = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_forecast_runs WHERE engine_version=?",
                    (engine,),
                ).fetchone()[0]
            )
            if "v6_forecast_runs" in tables
            else 0
        )
        normalized_outcomes = (
            int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_forecast_outcomes o
                    JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (engine,),
                ).fetchone()[0]
            )
            if {"v6_forecast_runs", "v6_forecast_outcomes"}.issubset(tables)
            else 0
        )
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    return {
        "schema_version": LEGACY_MIGRATION_SCHEMA_VERSION,
        "status": "migration_needed"
        if legacy_signals > normalized_signals or legacy_outcomes > normalized_outcomes
        else "already_covered",
        "apply_required": legacy_signals > normalized_signals or legacy_outcomes > normalized_outcomes,
        "engine_version": engine,
        "legacy_signals": legacy_signals,
        "normalized_signals": normalized_signals,
        "legacy_outcomes": legacy_outcomes,
        "normalized_outcomes": normalized_outcomes,
        "quick_check": quick,
        "foreign_key_errors": len(fk_errors),
        "runtime_policy": "explicit_cli_only",
        "drop_legacy_tables": False,
    }


def migrate_legacy_to_normalized(
    v6_db_path: str | Path,
    *,
    engine_version: str,
    apply: bool = False,
    report_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Dry-run by default; mutate only when an operator passes apply=True."""
    plan = plan_legacy_migration(v6_db_path, engine_version=engine_version)
    if not apply or not plan["apply_required"]:
        return {
            **plan,
            "mode": "dry_run" if not apply else "no_op",
            "applied": False,
        }

    engine = str(engine_version).strip()
    with _connect_ro(v6_db_path) as conn:
        board = [
            {"id": int(row["id"]), "code": str(row["code"] or "").strip().upper()}
            for row in conn.execute(
                "SELECT id, code FROM v6_signals WHERE engine_version=? ORDER BY id",
                (engine,),
            ).fetchall()
        ]

    day = (report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10]
    summary = NormalizedV6Persistence(str(v6_db_path)).persist_snapshot(
        {
            "version": engine,
            "generated_at": _utc_now(),
            "board": board,
        },
        source_engine_version=engine,
        report_date=day,
        run_mode="SHADOW",
        metadata={
            "migration_phase": "explicit_legacy_archive_migration",
            "migration_only": True,
            "requested_by": "operator_cli",
            "legacy_role": "historical_read_only_explicit_migration_source",
        },
    )
    after = plan_legacy_migration(v6_db_path, engine_version=engine)
    if after["apply_required"]:
        raise RuntimeError(
            "explicit legacy migration did not reach normalized coverage: " + repr(after)
        )
    return {
        **after,
        "mode": "apply",
        "applied": True,
        "migration_summary": summary,
    }
