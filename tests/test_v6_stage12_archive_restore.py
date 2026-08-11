from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.v6_daily.legacy_archive import (
    export_verified_legacy_archive,
    inspect_legacy_facts,
    restore_verified_legacy_archive,
    verify_legacy_archive,
)
from src.v6_daily.legacy_retirement_gate_v6 import (
    assert_legacy_retirement_gate_v6,
    evaluate_legacy_retirement_gate_v6,
)
from src.v6_daily.normalized_schema import (
    ACCURACY_LAB_MIGRATION_ID,
    NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION,
    ensure_normalized_schema,
)
from src.v6_daily.production_import_guard import assert_production_import_graph_clean
from tests.test_v6_stage11_schema_archival import ENGINE, _create_legacy_history


def test_verified_archive_manifest_is_deterministic_and_source_read_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    _create_legacy_history(source)
    before = inspect_legacy_facts(source)

    first = export_verified_legacy_archive(
        source,
        tmp_path / "archive-1.json",
        source_commit="stage12-test-commit",
        engine_version=ENGINE,
    )
    second = export_verified_legacy_archive(
        source,
        tmp_path / "archive-2.json",
        source_commit="stage12-test-commit",
        engine_version=ENGINE,
    )
    after = inspect_legacy_facts(source)

    assert first["status"] == "verified_archive_exported"
    assert first["manifest_version"] == "v6-legacy-archive-manifest-v2"
    assert first["content_sha256"] == second["content_sha256"]
    assert first["schema_snapshot_sha256"] == second["schema_snapshot_sha256"]
    assert first["legacy_signal_rows"] == 1
    assert first["legacy_outcome_rows"] == 1
    assert first["source_identity"]["source_commit"] == "stage12-test-commit"
    assert first["source_identity"]["requested_engine_version"] == ENGINE
    assert first["source_identity"]["engine_versions"] == [ENGINE]
    assert first["source_mutated"] is False
    assert first["source_unchanged"] is True
    assert before["tables"] == after["tables"]

    manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
    assert manifest["archive_file_sha256"] == first["archive_file_sha256"]
    assert manifest["archive_content_sha256"] == first["content_sha256"]
    assert manifest["schema_snapshot_sha256"] == first["schema_snapshot_sha256"]
    assert manifest["tables"]["v6_signals"]["row_count"] == 1
    assert manifest["tables"]["v6_outcomes"]["row_count"] == 1
    assert manifest["tables"]["v6_signals"]["row_hashes_sha256"]
    assert manifest["tables"]["v6_outcomes"]["row_hashes_sha256"]

    verification = verify_legacy_archive(first["output"])
    assert verification["verified"] is True
    assert verification["errors"] == []


def test_verified_archive_restores_exact_schema_and_rows_in_isolation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    _create_legacy_history(source)
    archive = export_verified_legacy_archive(
        source,
        tmp_path / "legacy-archive.json",
        source_commit="restore-test",
        engine_version=ENGINE,
    )
    restored = tmp_path / "restored.db"

    result = restore_verified_legacy_archive(archive["output"], restored)

    assert result["status"] == "pass"
    assert result["verified"] is True
    assert result["archive_verified"] is True
    assert result["isolated_restore"] is True
    assert result["source_database_mutated"] is False
    assert result["quick_check"].lower() == "ok"
    assert result["foreign_key_errors"] == 0
    assert result["archive_content_sha256"] == archive["content_sha256"]
    assert result["schema_snapshot_sha256"] == archive["schema_snapshot_sha256"]
    assert result["restored_schema_snapshot_sha256"] == archive["schema_snapshot_sha256"]
    assert inspect_legacy_facts(source)["tables"] == inspect_legacy_facts(restored)["tables"]


def test_archive_tampering_is_rejected_before_restore(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    _create_legacy_history(source)
    archive = export_verified_legacy_archive(
        source,
        tmp_path / "legacy-archive.json",
        source_commit="tamper-test",
        engine_version=ENGINE,
    )
    archive_path = Path(archive["output"])
    payload = json.loads(archive_path.read_text(encoding="utf-8"))
    payload["tables"]["v6_signals"]["rows"][0]["code"] = "TAMPERED"
    archive_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    verification = verify_legacy_archive(archive_path)
    assert verification["verified"] is False
    assert verification["errors"]
    with pytest.raises(RuntimeError, match="legacy archive verification failed"):
        restore_verified_legacy_archive(archive_path, tmp_path / "should-not-restore.db")


def test_accuracy_lab_schema_is_owned_by_unified_registry(tmp_path: Path) -> None:
    schema_db = tmp_path / "schema.db"
    registry = ensure_normalized_schema(schema_db)
    migration_ids = {item["migration_id"] for item in registry["migrations"]}

    assert registry["status"] == "current"
    assert registry["pending_migrations"] == []
    assert registry["accuracy_lab_schema_version"] == NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION
    assert ACCURACY_LAB_MIGRATION_ID in migration_ids
    assert registry["migration_count"] >= 2

    import sqlite3

    with sqlite3.connect(str(schema_db)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "v6_accuracy_shadow_forecasts",
        "v6_accuracy_shadow_outcomes",
        "v6_accuracy_trade_outcomes",
    }.issubset(tables)
    assert "v6_signals" not in tables
    assert "v6_outcomes" not in tables


def test_retirement_gate_v6_requires_verified_restore_and_never_drops_now(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    _create_legacy_history(source)
    before = inspect_legacy_facts(source)
    archive = export_verified_legacy_archive(
        source,
        tmp_path / "legacy-archive.json",
        source_commit="gate-v6-test",
        engine_version=ENGINE,
    )
    restore = restore_verified_legacy_archive(
        archive["output"],
        tmp_path / "restored.db",
    )
    registry = ensure_normalized_schema(tmp_path / "schema.db")
    import_guard = assert_production_import_graph_clean(Path(__file__).resolve().parents[1])
    after = inspect_legacy_facts(source)

    gate = assert_legacy_retirement_gate_v6(
        archive=archive,
        restore=restore,
        schema_registry=registry,
        import_guard=import_guard,
        source_unchanged=before["tables"] == after["tables"],
    )
    assert gate["status"] == "ready_for_stage13"
    assert gate["stage13_eligible"] is True
    assert gate["physical_drop_allowed_now"] is False
    assert gate["requires_separate_stage13_change"] is True
    assert gate["accuracy_lab_migration_registered"] is True
    assert gate["forbidden_import_count"] == 0

    broken_restore = dict(restore)
    broken_restore["verified"] = False
    blocked = evaluate_legacy_retirement_gate_v6(
        archive=archive,
        restore=broken_restore,
        schema_registry=registry,
        import_guard=import_guard,
        source_unchanged=True,
    )
    assert blocked["stage13_eligible"] is False
    assert blocked["status"] == "blocked"
    assert blocked["reasons"]
