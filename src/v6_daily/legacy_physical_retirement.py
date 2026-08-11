from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from .legacy_archive import (
    LEGACY_FACT_TABLES,
    export_verified_legacy_archive,
    inspect_legacy_facts,
    restore_verified_legacy_archive,
)
from .legacy_retirement_gate_v6 import assert_legacy_retirement_gate_v6
from .normalized_persistence import NormalizedV6Persistence
from .normalized_schema import ensure_normalized_schema, normalized_schema_status
from .production_import_guard import assert_production_import_graph_clean


LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION = "v6-legacy-physical-retirement-v1"
LEGACY_RETIREMENT_RECEIPT_VERSION = "v6-legacy-retirement-receipt-v1"
LEGACY_DROP_ORDER = ("v6_outcomes", "v6_signals")
PRODUCTION_ENTRY_MODULE = "scripts.run_v6_daily_stage11"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _database_state(path: str | Path) -> Dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return {
            "database_present": False,
            "legacy_tables_present": [],
            "legacy_tables_absent": True,
            "quick_check": "missing_database",
            "foreign_key_errors": 0,
        }
    with _connect(target) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    legacy_present = [table for table in LEGACY_FACT_TABLES if table in tables]
    return {
        "database_present": True,
        "legacy_tables_present": legacy_present,
        "legacy_tables_absent": not legacy_present,
        "quick_check": quick,
        "foreign_key_errors": len(fk_errors),
    }


def normalized_legacy_coverage(path: str | Path) -> Dict[str, Any]:
    """Prove legacy identities and key business identity fields exist normalized."""
    target = Path(path)
    if not target.is_file():
        return {
            "status": "not_required",
            "coverage_ready": True,
            "legacy_signal_ids": 0,
            "legacy_outcome_ids": 0,
            "normalized_signal_ids": 0,
            "normalized_outcome_ids": 0,
            "missing_signal_ids": [],
            "missing_outcome_ids": [],
            "mismatched_signal_ids": [],
            "mismatched_outcome_ids": [],
        }
    with _connect(target) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        legacy_signal_ids = (
            {int(row[0]) for row in conn.execute("SELECT id FROM v6_signals").fetchall()}
            if "v6_signals" in tables
            else set()
        )
        legacy_outcome_ids = (
            {int(row[0]) for row in conn.execute("SELECT id FROM v6_outcomes").fetchall()}
            if "v6_outcomes" in tables
            else set()
        )
        normalized_signal_ids = (
            {
                int(row[0])
                for row in conn.execute(
                    "SELECT source_signal_id FROM v6_forecast_runs WHERE source_signal_id IS NOT NULL"
                ).fetchall()
            }
            if "v6_forecast_runs" in tables
            else set()
        )
        normalized_outcome_ids = (
            {
                int(row[0])
                for row in conn.execute(
                    "SELECT source_outcome_id FROM v6_forecast_outcomes WHERE source_outcome_id IS NOT NULL"
                ).fetchall()
            }
            if "v6_forecast_outcomes" in tables
            else set()
        )

        mismatched_signal_ids: list[int] = []
        if {"v6_signals", "v6_forecast_runs"}.issubset(tables):
            rows = conn.execute(
                """
                SELECT s.id
                FROM v6_signals s
                JOIN v6_forecast_runs f ON f.source_signal_id=s.id
                WHERE COALESCE(f.engine_version,'') <> COALESCE(s.engine_version,'')
                   OR COALESCE(f.analysis_history_id,-1) <> COALESCE(s.analysis_history_id,-1)
                   OR UPPER(COALESCE(f.symbol,'')) <> UPPER(COALESCE(s.code,''))
                   OR COALESCE(f.direction,'') <> COALESCE(s.direction,'')
                ORDER BY s.id
                """
            ).fetchall()
            mismatched_signal_ids = [int(row[0]) for row in rows]

        mismatched_outcome_ids: list[int] = []
        if {"v6_outcomes", "v6_forecast_outcomes"}.issubset(tables):
            rows = conn.execute(
                """
                SELECT o.id
                FROM v6_outcomes o
                JOIN v6_forecast_outcomes n ON n.source_outcome_id=o.id
                WHERE COALESCE(n.source_signal_id,-1) <> COALESCE(o.signal_id,-1)
                   OR COALESCE(n.horizon_days,-1) <> COALESCE(o.horizon_days,-1)
                   OR ABS(COALESCE(n.return_pct,0.0) - COALESCE(o.return_pct,0.0)) > 0.000000001
                ORDER BY o.id
                """
            ).fetchall()
            mismatched_outcome_ids = [int(row[0]) for row in rows]

    missing_signal_ids = sorted(legacy_signal_ids - normalized_signal_ids)
    missing_outcome_ids = sorted(legacy_outcome_ids - normalized_outcome_ids)
    ready = (
        not missing_signal_ids
        and not missing_outcome_ids
        and not mismatched_signal_ids
        and not mismatched_outcome_ids
    )
    return {
        "status": "covered" if ready else "incomplete",
        "coverage_ready": ready,
        "legacy_signal_ids": len(legacy_signal_ids),
        "legacy_outcome_ids": len(legacy_outcome_ids),
        "normalized_signal_ids": len(normalized_signal_ids),
        "normalized_outcome_ids": len(normalized_outcome_ids),
        "missing_signal_ids": missing_signal_ids,
        "missing_outcome_ids": missing_outcome_ids,
        "mismatched_signal_ids": mismatched_signal_ids,
        "mismatched_outcome_ids": mismatched_outcome_ids,
    }


def migrate_missing_legacy_coverage(
    path: str | Path,
    *,
    report_date: str | None = None,
) -> Dict[str, Any]:
    """One-time Stage 13 bridge: normalize all legacy engines before retirement.

    This intentionally mutates only normalized tables. Legacy facts are
    fingerprinted before/after and must remain byte-for-byte equivalent at the
    logical table level. Existing normalized identities with conflicting core
    fields are never overwritten; the final coverage check will fail closed.
    """
    target = Path(path)
    before_facts = inspect_legacy_facts(target)
    before = normalized_legacy_coverage(target)
    if before.get("coverage_ready") is True:
        return {
            "status": "already_covered",
            "applied": False,
            "before": before,
            "after": before,
            "engines": [],
            "legacy_source_unchanged": True,
        }

    with _connect(target) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "v6_signals" not in tables:
            return {
                "status": "not_required",
                "applied": False,
                "before": before,
                "after": before,
                "engines": [],
                "legacy_source_unchanged": True,
            }
        engines = [
            str(row[0]).strip()
            for row in conn.execute(
                "SELECT DISTINCT engine_version FROM v6_signals ORDER BY engine_version"
            ).fetchall()
            if str(row[0] or "").strip()
        ]
        board_by_engine = {
            engine: [
                {
                    "id": int(row[0]),
                    "code": str(row[1] or "").strip().upper(),
                }
                for row in conn.execute(
                    "SELECT id, code FROM v6_signals WHERE engine_version=? ORDER BY id",
                    (engine,),
                ).fetchall()
            ]
            for engine in engines
        }

    day = (report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10]
    persistence = NormalizedV6Persistence(str(target))
    summaries: list[Dict[str, Any]] = []
    for engine in engines:
        summary = persistence.persist_snapshot(
            {
                "version": engine,
                "generated_at": _utc_now(),
                "board": board_by_engine[engine],
            },
            source_engine_version=engine,
            report_date=day,
            run_mode="SHADOW",
            metadata={
                "migration_phase": "stage13_pre_retirement_coverage",
                "migration_only": True,
                "requested_by": "stage13_explicit_apply",
                "legacy_role": "retiring_after_verified_archive_restore",
            },
        )
        summaries.append(summary)

    after = normalized_legacy_coverage(target)
    after_facts = inspect_legacy_facts(target)
    source_unchanged = before_facts.get("tables") == after_facts.get("tables")
    if not source_unchanged:
        raise RuntimeError("Stage 13 coverage migration mutated legacy source facts")
    if after.get("coverage_ready") is not True:
        raise RuntimeError(
            "Stage 13 coverage migration did not reach exact normalized coverage: "
            + repr(after)
        )
    return {
        "status": "covered",
        "applied": True,
        "before": before,
        "after": after,
        "engines": engines,
        "migration_summaries": summaries,
        "legacy_source_unchanged": source_unchanged,
    }


def _write_receipt(receipt_path: str | Path | None, payload: Mapping[str, Any]) -> None:
    if receipt_path is None:
        return
    target = Path(receipt_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _base_receipt(
    *,
    v6_db_path: str | Path,
    apply: bool,
    before: Mapping[str, Any],
    source_commit: str | None,
) -> Dict[str, Any]:
    return {
        "schema_version": LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION,
        "receipt_version": LEGACY_RETIREMENT_RECEIPT_VERSION,
        "created_at": _utc_now(),
        "database": str(v6_db_path),
        "source_commit": str(source_commit or "").strip() or "unknown",
        "apply_requested": bool(apply),
        "drop_order": list(LEGACY_DROP_ORDER),
        "before": dict(before),
        "policy": {
            "normalized_coverage_required": True,
            "coverage_migration_requires_explicit_flag": True,
            "archive_before_drop": True,
            "verified_restore_before_drop": True,
            "transactional_drop": True,
            "production_entry_module": PRODUCTION_ENTRY_MODULE,
            "automatic_reverse_projection": False,
        },
    }


def build_physical_retirement_evidence(
    v6_db_path: str | Path,
    *,
    archive_dir: str | Path,
    repo_root: str | Path,
    source_commit: str | None = None,
    engine_version: str | None = None,
) -> Dict[str, Any]:
    """Create the fail-closed coverage/archive/restore evidence required before DROP."""
    target = Path(v6_db_path)
    if not target.is_file():
        raise FileNotFoundError(str(target))

    schema_registry = ensure_normalized_schema(target)
    coverage = normalized_legacy_coverage(target)
    if coverage.get("coverage_ready") is not True:
        raise RuntimeError(
            "Stage 13 normalized coverage incomplete; refusing legacy DROP: "
            + repr(coverage)
        )

    import_guard = assert_production_import_graph_clean(
        repo_root,
        entry_module=PRODUCTION_ENTRY_MODULE,
    )
    before = inspect_legacy_facts(target)

    output_dir = Path(archive_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / "legacy_archive.json"
    manifest_path = output_dir / "legacy_archive.manifest.json"
    restore_path = output_dir / "legacy_restore_rehearsal.db"
    for artifact in (archive_path, manifest_path, restore_path):
        if artifact.exists():
            artifact.unlink()

    archive = export_verified_legacy_archive(
        target,
        archive_path,
        manifest_path=manifest_path,
        source_commit=source_commit,
        engine_version=engine_version,
    )
    restore = restore_verified_legacy_archive(
        archive_path,
        restore_path,
        manifest_path=manifest_path,
    )
    after = inspect_legacy_facts(target)
    source_unchanged = before.get("tables") == after.get("tables")
    gate_v6 = assert_legacy_retirement_gate_v6(
        archive=archive,
        restore=restore,
        schema_registry=schema_registry,
        import_guard=import_guard,
        source_unchanged=source_unchanged,
    )

    return {
        "status": "verified",
        "normalized_coverage": coverage,
        "archive": archive,
        "restore": restore,
        "gate_v6": gate_v6,
        "schema_registry": schema_registry,
        "production_import_guard": import_guard,
        "source_unchanged": source_unchanged,
        "legacy_before": before,
        "legacy_after_evidence": after,
    }


def retire_legacy_tables(
    v6_db_path: str | Path,
    *,
    archive_dir: str | Path,
    repo_root: str | Path,
    receipt_path: str | Path | None = None,
    source_commit: str | None = None,
    engine_version: str | None = None,
    report_date: str | None = None,
    migrate_missing_coverage: bool = False,
    apply: bool = False,
) -> Dict[str, Any]:
    """Retire legacy facts after exact normalized coverage + verified restore.

    Dry-run is the API default. ``apply=True`` is required to DROP. If an old
    production cache predates complete normalized coverage, the caller must also
    explicitly opt into ``migrate_missing_coverage=True``; that bridge writes
    normalized tables only, verifies legacy source immutability, and then repeats
    the exact coverage check before archival or DROP.
    """
    target = Path(v6_db_path)
    before = _database_state(target)
    receipt = _base_receipt(
        v6_db_path=target,
        apply=apply,
        before=before,
        source_commit=source_commit,
    )

    if not target.is_file():
        receipt.update(
            {
                "status": "already_retired",
                "action": "no_database_before_run",
                "legacy_tables_absent": True,
                "archive_required": False,
                "coverage_migration": {"status": "not_required", "applied": False},
                "normalized_coverage": normalized_legacy_coverage(target),
                "dropped_tables": [],
                "after": before,
            }
        )
        _write_receipt(receipt_path, receipt)
        return receipt

    schema_registry = ensure_normalized_schema(target)
    before = _database_state(target)
    coverage = normalized_legacy_coverage(target)
    receipt["before"] = before
    receipt["normalized_coverage_before"] = coverage
    receipt["schema_registry_before"] = schema_registry

    if before["legacy_tables_absent"]:
        receipt.update(
            {
                "status": "already_retired",
                "action": "none",
                "legacy_tables_absent": True,
                "archive_required": False,
                "coverage_migration": {"status": "not_required", "applied": False},
                "normalized_coverage": coverage,
                "dropped_tables": [],
                "after": before,
                "schema_registry_after": normalized_schema_status(target),
            }
        )
        _write_receipt(receipt_path, receipt)
        return receipt

    coverage_migration: Dict[str, Any] = {
        "status": "not_required" if coverage.get("coverage_ready") else "not_requested",
        "applied": False,
    }
    if coverage.get("coverage_ready") is not True:
        if not migrate_missing_coverage:
            raise RuntimeError(
                "Stage 13 normalized coverage incomplete; refusing legacy DROP: "
                + repr(coverage)
            )
        if not apply:
            raise RuntimeError(
                "Stage 13 coverage migration is only allowed together with explicit apply=True"
            )
        coverage_migration = globals()["migrate_missing_legacy_coverage"](
            target,
            report_date=report_date,
        )
        coverage = normalized_legacy_coverage(target)

    receipt["coverage_migration"] = coverage_migration
    receipt["normalized_coverage"] = coverage
    if coverage.get("coverage_ready") is not True:
        raise RuntimeError(
            "Stage 13 normalized coverage incomplete after migration; refusing legacy DROP: "
            + repr(coverage)
        )

    evidence = build_physical_retirement_evidence(
        target,
        archive_dir=archive_dir,
        repo_root=repo_root,
        source_commit=source_commit,
        engine_version=engine_version,
    )
    receipt["evidence"] = evidence
    receipt["archive_required"] = True
    receipt["would_drop_tables"] = [
        table for table in LEGACY_DROP_ORDER if table in before["legacy_tables_present"]
    ]

    if not apply:
        receipt.update(
            {
                "status": "dry_run",
                "action": "would_retire",
                "legacy_tables_absent": False,
                "dropped_tables": [],
                "after": _database_state(target),
            }
        )
        _write_receipt(receipt_path, receipt)
        return receipt

    gate_v6 = evidence.get("gate_v6") or {}
    if gate_v6.get("stage13_eligible") is not True:
        raise RuntimeError(f"Stage 13 retirement blocked by Gate v6: {gate_v6!r}")
    if evidence.get("source_unchanged") is not True:
        raise RuntimeError("Stage 13 retirement blocked: archive evidence changed source facts")
    if (evidence.get("normalized_coverage") or {}).get("coverage_ready") is not True:
        raise RuntimeError("Stage 13 retirement blocked: normalized coverage evidence is incomplete")

    with _connect(target) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current_tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            dropped: list[str] = []
            for table in LEGACY_DROP_ORDER:
                if table in current_tables:
                    conn.execute(f"DROP TABLE {table}")
                    dropped.append(table)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    after = _database_state(target)
    registry_after = normalized_schema_status(target)
    import_guard_after = assert_production_import_graph_clean(
        repo_root,
        entry_module=PRODUCTION_ENTRY_MODULE,
    )

    errors: list[str] = []
    if after["legacy_tables_absent"] is not True:
        errors.append(f"legacy tables remain: {after['legacy_tables_present']!r}")
    if str(after["quick_check"]).strip().lower() != "ok":
        errors.append(f"quick_check={after['quick_check']!r}")
    if int(after["foreign_key_errors"] or 0) != 0:
        errors.append(f"foreign_key_errors={after['foreign_key_errors']!r}")
    if registry_after.get("status") != "current":
        errors.append(f"normalized schema registry={registry_after.get('status')!r}")
    if import_guard_after.get("status") != "clean":
        errors.append("production import graph is not clean after retirement")
    if errors:
        raise RuntimeError("Stage 13 post-drop verification failed: " + "; ".join(errors))

    receipt.update(
        {
            "status": "retired",
            "action": "transactional_drop",
            "legacy_tables_absent": True,
            "dropped_tables": dropped,
            "after": after,
            "schema_registry_after": registry_after,
            "production_import_guard_after": import_guard_after,
            "archive_verified": (evidence.get("archive") or {}).get("status")
            == "verified_archive_exported",
            "restore_verified": (evidence.get("restore") or {}).get("verified") is True,
            "gate_v6_passed": gate_v6.get("stage13_eligible") is True,
        }
    )
    _write_receipt(receipt_path, receipt)
    return receipt
