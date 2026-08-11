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
    """Create the fail-closed archive/restore evidence required before DROP."""
    target = Path(v6_db_path)
    if not target.is_file():
        raise FileNotFoundError(str(target))

    schema_registry = ensure_normalized_schema(target)
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
    for path in (archive_path, manifest_path, restore_path):
        if path.exists():
            path.unlink()

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
    apply: bool = False,
) -> Dict[str, Any]:
    """Retire legacy fact tables only after a verified archive and restore.

    Dry-run is the API default. ``apply=True`` is intentionally required to
    perform the transactional DROP. The operation is idempotent: databases that
    already lack both legacy tables return ``already_retired`` without creating
    or mutating legacy facts.
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
                "dropped_tables": [],
                "after": before,
            }
        )
        _write_receipt(receipt_path, receipt)
        return receipt

    schema_registry = ensure_normalized_schema(target)
    before = _database_state(target)
    receipt["before"] = before
    receipt["schema_registry_before"] = schema_registry

    if before["legacy_tables_absent"]:
        receipt.update(
            {
                "status": "already_retired",
                "action": "none",
                "legacy_tables_absent": True,
                "archive_required": False,
                "dropped_tables": [],
                "after": before,
                "schema_registry_after": normalized_schema_status(target),
            }
        )
        _write_receipt(receipt_path, receipt)
        return receipt

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
