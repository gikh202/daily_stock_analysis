from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict


LEGACY_WRITE_GUARD_SCHEMA_VERSION = "v6-legacy-write-guard-v1"
LEGACY_FACT_TABLES = ("v6_signals", "v6_outcomes")


def _row_digest(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(columns, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    for row in rows:
        digest.update(b"\n")
        digest.update(
            json.dumps(
                list(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    return digest.hexdigest()


def snapshot_legacy_facts(v6_db_path: str | Path) -> Dict[str, Any]:
    """Capture a deterministic read-only fingerprint of legacy V6 fact tables."""
    path = Path(v6_db_path)
    if not path.is_file():
        return {
            "database_present": False,
            "tables": {},
        }

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        result: Dict[str, Any] = {}
        for table in LEGACY_FACT_TABLES:
            if table not in tables:
                result[table] = {
                    "present": False,
                    "rows": 0,
                    "sha256": None,
                }
                continue
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY id ASC")
            columns = [str(item[0]) for item in cursor.description or []]
            rows = [tuple(row) for row in cursor.fetchall()]
            result[table] = {
                "present": True,
                "rows": len(rows),
                "sha256": _row_digest(columns, rows),
            }
        return {
            "database_present": True,
            "tables": result,
        }
    finally:
        conn.close()


def compare_legacy_snapshots(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, Any]:
    unchanged = before == after
    changes: list[Dict[str, Any]] = []
    before_tables = dict(before.get("tables") or {})
    after_tables = dict(after.get("tables") or {})
    for table in LEGACY_FACT_TABLES:
        left = before_tables.get(table)
        right = after_tables.get(table)
        if left != right:
            changes.append({"table": table, "before": left, "after": right})
    if bool(before.get("database_present")) != bool(after.get("database_present")):
        changes.append(
            {
                "table": "__database__",
                "before": before.get("database_present"),
                "after": after.get("database_present"),
            }
        )
    return {
        "schema_version": LEGACY_WRITE_GUARD_SCHEMA_VERSION,
        "status": "unchanged" if unchanged else "changed",
        "legacy_writes_detected": not unchanged,
        "legacy_projection_enabled": False,
        "legacy_projection_writes": 0 if unchanged else None,
        "before": before,
        "after": after,
        "changes": changes,
    }


def assert_legacy_facts_unchanged(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, Any]:
    guard = compare_legacy_snapshots(before, after)
    if guard["legacy_writes_detected"]:
        raise RuntimeError(
            "legacy V6 fact tables changed during normalized-only production run: "
            + repr(guard.get("changes"))
        )
    return guard
