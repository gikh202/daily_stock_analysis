from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .normalized_persistence import NormalizedV6Persistence


LEGACY_ARCHIVE_SCHEMA_VERSION = "v6-legacy-verified-archive-v2"
LEGACY_ARCHIVE_MANIFEST_VERSION = "v6-legacy-archive-manifest-v2"
LEGACY_RESTORE_SCHEMA_VERSION = "v6-legacy-restore-rehearsal-v1"
LEGACY_MIGRATION_SCHEMA_VERSION = "v6-explicit-legacy-migration-v1"
LEGACY_FACT_TABLES = ("v6_signals", "v6_outcomes")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _encode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "__sqlite_type__": "blob",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, Mapping) and value.get("__sqlite_type__") == "blob":
        return base64.b64decode(str(value.get("base64") or ""))
    return value


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _connect_ro(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _present_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _capture_table(conn: sqlite3.Connection, table: str) -> Dict[str, Any]:
    if table not in _present_tables(conn):
        return {
            "present": False,
            "columns": [],
            "row_count": 0,
            "rows": [],
            "row_hashes": [],
            "row_hashes_sha256": _hash([]),
            "table_sha256": _hash({"columns": [], "rows": []}),
        }
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY id ASC")
    columns = [str(item[0]) for item in cursor.description or []]
    rows = [
        {column: _encode_value(row[column]) for column in columns}
        for row in cursor.fetchall()
    ]
    row_hashes = [_hash(row) for row in rows]
    return {
        "present": True,
        "columns": columns,
        "row_count": len(rows),
        "rows": rows,
        "row_hashes": row_hashes,
        "row_hashes_sha256": _hash(row_hashes),
        "table_sha256": _hash({"columns": columns, "rows": rows}),
    }


def _capture_schema_objects(conn: sqlite3.Connection) -> list[Dict[str, Any]]:
    placeholders = ",".join("?" for _ in LEGACY_FACT_TABLES)
    rows = conn.execute(
        f"""
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE tbl_name IN ({placeholders})
          AND type IN ('table', 'index', 'trigger')
        ORDER BY
          CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,
          tbl_name,
          name
        """,
        LEGACY_FACT_TABLES,
    ).fetchall()
    return [
        {
            "type": str(row["type"]),
            "name": str(row["name"]),
            "table": str(row["tbl_name"]),
            "sql": None if row["sql"] is None else str(row["sql"]),
        }
        for row in rows
    ]


def _source_identity(
    conn: sqlite3.Connection,
    *,
    source_commit: Optional[str],
    engine_version: Optional[str],
) -> Dict[str, Any]:
    tables = _present_tables(conn)
    engine_versions: list[str] = []
    if "v6_signals" in tables:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(v6_signals)").fetchall()
        }
        if "engine_version" in columns:
            engine_versions = [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT engine_version FROM v6_signals "
                    "WHERE engine_version IS NOT NULL AND TRIM(engine_version)<>'' "
                    "ORDER BY engine_version"
                ).fetchall()
            ]
    requested_engine = str(engine_version or "").strip() or None
    if requested_engine and engine_versions and requested_engine not in engine_versions:
        raise ValueError(
            f"requested engine_version {requested_engine!r} is absent from legacy facts: "
            f"{engine_versions!r}"
        )
    return {
        "source_commit": str(source_commit or "").strip() or "unknown",
        "requested_engine_version": requested_engine,
        "engine_versions": engine_versions,
        "sqlite_user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
    }


def inspect_legacy_facts(v6_db_path: str | Path) -> Dict[str, Any]:
    with _connect_ro(v6_db_path) as conn:
        result: Dict[str, Any] = {}
        for table in LEGACY_FACT_TABLES:
            snapshot = _capture_table(conn, table)
            result[table] = {
                "present": snapshot["present"],
                "columns": snapshot["columns"],
                "rows": snapshot["row_count"],
                "sha256": snapshot["table_sha256"],
                "row_hashes_sha256": snapshot["row_hashes_sha256"],
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


def export_verified_legacy_archive(
    v6_db_path: str | Path,
    output_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    source_commit: Optional[str] = None,
    engine_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Export legacy facts plus an independently verifiable manifest.

    The source database is opened read-only. The archive data file contains the
    schema snapshot and typed rows. A sidecar manifest pins the archive file
    SHA256, canonical content SHA256, per-table row hashes, source commit and
    engine identity. Timestamps and file paths are excluded from content hashes
    so repeated exports of the same source facts remain deterministic.
    """
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target = (
        Path(manifest_path)
        if manifest_path is not None
        else target.with_name(target.name + ".manifest.json")
    )
    manifest_target.parent.mkdir(parents=True, exist_ok=True)

    before = inspect_legacy_facts(v6_db_path)
    with _connect_ro(v6_db_path) as conn:
        tables = {table: _capture_table(conn, table) for table in LEGACY_FACT_TABLES}
        schema_objects = _capture_schema_objects(conn)
        identity = _source_identity(
            conn,
            source_commit=source_commit,
            engine_version=engine_version,
        )
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]

    if quick.strip().lower() != "ok" or fk_rows:
        raise RuntimeError(
            "legacy archive source integrity failed: "
            f"quick={quick!r} foreign_key_errors={len(fk_rows)}"
        )

    schema_snapshot = {
        "objects": schema_objects,
        "sha256": _hash(schema_objects),
    }
    content_basis = {
        "source_identity": identity,
        "schema_snapshot": schema_snapshot,
        "tables": tables,
    }
    created_at = _utc_now()
    archive_payload = {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "restore_contract": LEGACY_RESTORE_SCHEMA_VERSION,
        "created_at": created_at,
        "source_identity": identity,
        "source_integrity": {
            "quick_check": quick,
            "foreign_key_errors": len(fk_rows),
        },
        "schema_snapshot": schema_snapshot,
        "tables": tables,
        "content_sha256": _hash(content_basis),
    }
    target.write_text(
        json.dumps(archive_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    archive_file_sha256 = _file_hash(target)

    table_manifest = {
        table: {
            "present": block["present"],
            "columns": block["columns"],
            "row_count": block["row_count"],
            "row_hashes_sha256": block["row_hashes_sha256"],
            "table_sha256": block["table_sha256"],
        }
        for table, block in tables.items()
    }
    manifest = {
        "schema_version": LEGACY_ARCHIVE_MANIFEST_VERSION,
        "archive_schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "restore_contract": LEGACY_RESTORE_SCHEMA_VERSION,
        "created_at": created_at,
        "archive_file": target.name,
        "archive_file_sha256": archive_file_sha256,
        "archive_content_sha256": archive_payload["content_sha256"],
        "source_identity": identity,
        "source_integrity": archive_payload["source_integrity"],
        "schema_snapshot_sha256": schema_snapshot["sha256"],
        "tables": table_manifest,
    }
    manifest["manifest_content_sha256"] = _hash(manifest)
    manifest_target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    after = inspect_legacy_facts(v6_db_path)
    source_unchanged = before["tables"] == after["tables"]
    if not source_unchanged:
        raise RuntimeError("legacy archive source facts changed during read-only export")

    return {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "manifest_version": LEGACY_ARCHIVE_MANIFEST_VERSION,
        "status": "verified_archive_exported",
        "output": str(target),
        "manifest": str(manifest_target),
        "content_sha256": archive_payload["content_sha256"],
        "archive_file_sha256": archive_file_sha256,
        "manifest_file_sha256": _file_hash(manifest_target),
        "schema_snapshot_sha256": schema_snapshot["sha256"],
        "legacy_signal_rows": tables["v6_signals"]["row_count"],
        "legacy_outcome_rows": tables["v6_outcomes"]["row_count"],
        "source_identity": identity,
        "source_quick_check": quick,
        "source_foreign_key_errors": len(fk_rows),
        "source_mutated": False,
        "source_unchanged": True,
    }


def export_legacy_archive(
    v6_db_path: str | Path,
    output_path: str | Path,
) -> Dict[str, Any]:
    """Backward-compatible wrapper for the Stage 11 explicit archive CLI."""
    return export_verified_legacy_archive(v6_db_path, output_path)


def _load_archive(
    archive_path: str | Path,
    manifest_path: str | Path | None = None,
) -> tuple[Path, Path, Dict[str, Any], Dict[str, Any]]:
    archive_target = Path(archive_path)
    manifest_target = (
        Path(manifest_path)
        if manifest_path is not None
        else archive_target.with_name(archive_target.name + ".manifest.json")
    )
    if not archive_target.is_file():
        raise FileNotFoundError(str(archive_target))
    if not manifest_target.is_file():
        raise FileNotFoundError(str(manifest_target))
    archive_payload = json.loads(archive_target.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_target.read_text(encoding="utf-8"))
    return archive_target, manifest_target, archive_payload, manifest


def verify_legacy_archive(
    archive_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> Dict[str, Any]:
    archive_target, manifest_target, payload, manifest = _load_archive(
        archive_path,
        manifest_path,
    )
    errors: list[str] = []
    if payload.get("schema_version") != LEGACY_ARCHIVE_SCHEMA_VERSION:
        errors.append(f"archive schema mismatch: {payload.get('schema_version')!r}")
    if manifest.get("schema_version") != LEGACY_ARCHIVE_MANIFEST_VERSION:
        errors.append(f"manifest schema mismatch: {manifest.get('schema_version')!r}")
    if manifest.get("restore_contract") != LEGACY_RESTORE_SCHEMA_VERSION:
        errors.append(f"restore contract mismatch: {manifest.get('restore_contract')!r}")

    actual_file_hash = _file_hash(archive_target)
    if actual_file_hash != manifest.get("archive_file_sha256"):
        errors.append("archive file SHA256 mismatch")

    manifest_hash_basis = dict(manifest)
    expected_manifest_hash = manifest_hash_basis.pop("manifest_content_sha256", None)
    if _hash(manifest_hash_basis) != expected_manifest_hash:
        errors.append("manifest content SHA256 mismatch")

    schema_snapshot = payload.get("schema_snapshot") or {}
    schema_objects = schema_snapshot.get("objects") or []
    if _hash(schema_objects) != schema_snapshot.get("sha256"):
        errors.append("archive schema snapshot SHA256 mismatch")
    if schema_snapshot.get("sha256") != manifest.get("schema_snapshot_sha256"):
        errors.append("manifest schema snapshot SHA256 mismatch")

    tables = payload.get("tables") or {}
    for table in LEGACY_FACT_TABLES:
        block = tables.get(table) or {}
        columns = block.get("columns") or []
        rows = block.get("rows") or []
        row_hashes = [_hash(row) for row in rows]
        if row_hashes != (block.get("row_hashes") or []):
            errors.append(f"{table} row hash list mismatch")
        if _hash(row_hashes) != block.get("row_hashes_sha256"):
            errors.append(f"{table} row hash chain mismatch")
        if _hash({"columns": columns, "rows": rows}) != block.get("table_sha256"):
            errors.append(f"{table} table SHA256 mismatch")
        manifest_table = (manifest.get("tables") or {}).get(table) or {}
        if int(block.get("row_count") or 0) != len(rows):
            errors.append(f"{table} row count mismatch")
        for key in ("row_count", "row_hashes_sha256", "table_sha256"):
            if block.get(key) != manifest_table.get(key):
                errors.append(f"{table} manifest {key} mismatch")

    content_basis = {
        "source_identity": payload.get("source_identity") or {},
        "schema_snapshot": schema_snapshot,
        "tables": tables,
    }
    content_hash = _hash(content_basis)
    if content_hash != payload.get("content_sha256"):
        errors.append("archive canonical content SHA256 mismatch")
    if content_hash != manifest.get("archive_content_sha256"):
        errors.append("manifest canonical content SHA256 mismatch")

    verified = not errors
    return {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "manifest_version": LEGACY_ARCHIVE_MANIFEST_VERSION,
        "status": "verified" if verified else "invalid",
        "verified": verified,
        "archive": str(archive_target),
        "manifest": str(manifest_target),
        "archive_file_sha256": actual_file_hash,
        "archive_content_sha256": content_hash,
        "schema_snapshot_sha256": schema_snapshot.get("sha256"),
        "source_identity": payload.get("source_identity") or {},
        "errors": errors,
    }


def _restore_schema_objects(
    conn: sqlite3.Connection,
    schema_objects: list[Mapping[str, Any]],
) -> None:
    table_sql = {
        str(item.get("table")): str(item.get("sql") or "").strip()
        for item in schema_objects
        if item.get("type") == "table" and item.get("sql")
    }
    for table in LEGACY_FACT_TABLES:
        sql = table_sql.get(table)
        if sql:
            conn.execute(sql)
    for item in schema_objects:
        if item.get("type") not in {"index", "trigger"}:
            continue
        sql = str(item.get("sql") or "").strip()
        if sql:
            conn.execute(sql)


def restore_verified_legacy_archive(
    archive_path: str | Path,
    restore_db_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Restore a verified archive into an isolated SQLite file and prove parity."""
    verification = verify_legacy_archive(
        archive_path,
        manifest_path=manifest_path,
    )
    if not verification["verified"]:
        raise RuntimeError("legacy archive verification failed: " + repr(verification["errors"]))

    archive_target, _, payload, _ = _load_archive(archive_path, manifest_path)
    target = Path(restore_db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not overwrite:
            raise FileExistsError(str(target))
        target.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    schema_objects = list((payload.get("schema_snapshot") or {}).get("objects") or [])
    tables = payload.get("tables") or {}
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        table_objects = [item for item in schema_objects if item.get("type") == "table"]
        _restore_schema_objects(conn, table_objects)
        for table in LEGACY_FACT_TABLES:
            block = tables.get(table) or {}
            if not block.get("present"):
                continue
            columns = [str(value) for value in block.get("columns") or []]
            rows = list(block.get("rows") or [])
            if not columns:
                continue
            placeholders = ",".join("?" for _ in columns)
            column_sql = ",".join(f'"{column}"' for column in columns)
            statement = f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})"
            for row in rows:
                conn.execute(
                    statement,
                    tuple(_decode_value(row.get(column)) for column in columns),
                )
        non_table_objects = [item for item in schema_objects if item.get("type") != "table"]
        _restore_schema_objects(conn, non_table_objects)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        restored_schema_objects = _capture_schema_objects(conn)
        restored_tables = {
            table: _capture_table(conn, table)
            for table in LEGACY_FACT_TABLES
        }
    finally:
        conn.close()

    errors: list[str] = []
    if quick.strip().lower() != "ok":
        errors.append(f"restored quick_check={quick!r}")
    if fk_rows:
        errors.append(f"restored foreign_key_errors={len(fk_rows)}")
    expected_schema_hash = (payload.get("schema_snapshot") or {}).get("sha256")
    restored_schema_hash = _hash(restored_schema_objects)
    if restored_schema_hash != expected_schema_hash:
        errors.append("restored schema snapshot SHA256 mismatch")
    for table in LEGACY_FACT_TABLES:
        expected = tables.get(table) or {}
        actual = restored_tables.get(table) or {}
        for key in ("present", "columns", "row_count", "row_hashes_sha256", "table_sha256"):
            if expected.get(key) != actual.get(key):
                errors.append(f"restored {table} {key} mismatch")

    verified = not errors
    return {
        "schema_version": LEGACY_RESTORE_SCHEMA_VERSION,
        "status": "pass" if verified else "fail",
        "verified": verified,
        "archive": str(archive_target),
        "restore_database": str(target),
        "archive_verified": verification["verified"],
        "archive_content_sha256": verification["archive_content_sha256"],
        "schema_snapshot_sha256": expected_schema_hash,
        "restored_schema_snapshot_sha256": restored_schema_hash,
        "legacy_signal_rows": int((restored_tables.get("v6_signals") or {}).get("row_count") or 0),
        "legacy_outcome_rows": int((restored_tables.get("v6_outcomes") or {}).get("row_count") or 0),
        "quick_check": quick,
        "foreign_key_errors": len(fk_rows),
        "source_database_mutated": False,
        "isolated_restore": True,
        "errors": errors,
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
        tables = _present_tables(conn)
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
