from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.v6_daily.legacy_archive import inspect_legacy_facts, migrate_legacy_to_normalized
from src.v6_daily.legacy_physical_retirement import (
    LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION,
    LEGACY_RETIREMENT_RECEIPT_VERSION,
    normalized_legacy_coverage,
    retire_legacy_tables,
)
from src.v6_daily import production_gate_v7
from tests.test_v6_stage11_schema_archival import ENGINE, _create_legacy_history


REPO_ROOT = Path(__file__).resolve().parents[1]


def _migrated_legacy_db(tmp_path: Path) -> Path:
    path = tmp_path / "v6.db"
    _create_legacy_history(path)
    result = migrate_legacy_to_normalized(
        path,
        engine_version=ENGINE,
        apply=True,
        report_date="2026-08-11",
    )
    assert result["applied"] is True
    coverage = normalized_legacy_coverage(path)
    assert coverage["coverage_ready"] is True
    return path


def _normalized_counts(path: Path) -> tuple[int, int, int, int]:
    with sqlite3.connect(str(path)) as conn:
        return (
            int(conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0]),
            int(conn.execute("SELECT COUNT(*) FROM v6_decision_runs").fetchone()[0]),
            int(conn.execute("SELECT COUNT(*) FROM v6_execution_plans").fetchone()[0]),
            int(conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0]),
        )


def test_retirement_refuses_incomplete_normalized_coverage(tmp_path: Path) -> None:
    path = tmp_path / "legacy-only.db"
    _create_legacy_history(path)

    with pytest.raises(RuntimeError, match="normalized coverage incomplete"):
        retire_legacy_tables(
            path,
            archive_dir=tmp_path / "archive",
            receipt_path=tmp_path / "receipt.json",
            repo_root=REPO_ROOT,
            source_commit="stage13-incomplete",
            apply=True,
        )

    facts = inspect_legacy_facts(path)
    assert facts["tables"]["v6_signals"]["present"] is True
    assert facts["tables"]["v6_outcomes"]["present"] is True


def test_stage13_dry_run_builds_verified_evidence_without_drop(tmp_path: Path) -> None:
    path = _migrated_legacy_db(tmp_path)
    before = inspect_legacy_facts(path)

    result = retire_legacy_tables(
        path,
        archive_dir=tmp_path / "archive",
        receipt_path=tmp_path / "receipt.json",
        repo_root=REPO_ROOT,
        source_commit="stage13-dry-run",
        engine_version=ENGINE,
        apply=False,
    )
    after = inspect_legacy_facts(path)

    assert result["status"] == "dry_run"
    assert result["action"] == "would_retire"
    assert result["legacy_tables_absent"] is False
    assert result["normalized_coverage"]["coverage_ready"] is True
    assert result["evidence"]["archive"]["status"] == "verified_archive_exported"
    assert result["evidence"]["restore"]["verified"] is True
    assert result["evidence"]["gate_v6"]["stage13_eligible"] is True
    assert before["tables"] == after["tables"]


def test_stage13_apply_drops_legacy_tables_and_preserves_normalized_facts(
    tmp_path: Path,
) -> None:
    path = _migrated_legacy_db(tmp_path)
    normalized_before = _normalized_counts(path)

    result = retire_legacy_tables(
        path,
        archive_dir=tmp_path / "archive",
        receipt_path=tmp_path / "receipt.json",
        repo_root=REPO_ROOT,
        source_commit="stage13-apply",
        engine_version=ENGINE,
        apply=True,
    )

    with sqlite3.connect(str(path)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0]).lower()
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert result["schema_version"] == LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION
    assert result["receipt_version"] == LEGACY_RETIREMENT_RECEIPT_VERSION
    assert result["status"] == "retired"
    assert result["action"] == "transactional_drop"
    assert result["dropped_tables"] == ["v6_outcomes", "v6_signals"]
    assert result["legacy_tables_absent"] is True
    assert result["archive_verified"] is True
    assert result["restore_verified"] is True
    assert result["gate_v6_passed"] is True
    assert result["normalized_coverage"]["coverage_ready"] is True
    assert "v6_signals" not in tables
    assert "v6_outcomes" not in tables
    assert quick == "ok"
    assert fk_errors == []
    assert _normalized_counts(path) == normalized_before


def test_stage13_retirement_is_idempotent_after_drop(tmp_path: Path) -> None:
    path = _migrated_legacy_db(tmp_path)
    first = retire_legacy_tables(
        path,
        archive_dir=tmp_path / "archive-first",
        receipt_path=tmp_path / "receipt-first.json",
        repo_root=REPO_ROOT,
        source_commit="stage13-first",
        engine_version=ENGINE,
        apply=True,
    )
    normalized_after_first = _normalized_counts(path)

    second = retire_legacy_tables(
        path,
        archive_dir=tmp_path / "archive-second",
        receipt_path=tmp_path / "receipt-second.json",
        repo_root=REPO_ROOT,
        source_commit="stage13-second",
        engine_version=ENGINE,
        apply=True,
    )

    assert first["status"] == "retired"
    assert second["status"] == "already_retired"
    assert second["legacy_tables_absent"] is True
    assert second["dropped_tables"] == []
    assert second["archive_required"] is False
    assert _normalized_counts(path) == normalized_after_first


def test_gate_v7_requires_terminal_retirement_and_post_run_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        production_gate_v7,
        "assert_stage11_production_gate",
        lambda payload: {"schema_version": "v6-production-archival-gate-v5", "production_ready": True},
    )
    retirement = {
        "schema_version": LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION,
        "receipt_version": LEGACY_RETIREMENT_RECEIPT_VERSION,
        "status": "retired",
        "action": "transactional_drop",
        "legacy_tables_absent": True,
        "dropped_tables": ["v6_outcomes", "v6_signals"],
        "archive_verified": True,
        "restore_verified": True,
        "gate_v6_passed": True,
        "policy": {
            "normalized_coverage_required": True,
            "archive_before_drop": True,
            "verified_restore_before_drop": True,
            "transactional_drop": True,
            "automatic_reverse_projection": False,
        },
        "post_production": {
            "legacy_tables_present": [],
            "legacy_tables_absent": True,
            "quick_check": "ok",
            "foreign_key_errors": 0,
        },
    }
    payload = {
        "stage13_entrypoint": "v6-stage13-physical-legacy-retirement-v1",
        "physical_retirement": retirement,
    }

    gate = production_gate_v7.assert_stage13_production_gate(payload)
    assert gate["production_ready"] is True
    assert gate["legacy_tables_absent"] is True

    broken = dict(retirement)
    broken["post_production"] = {
        "legacy_tables_present": ["v6_signals"],
        "legacy_tables_absent": False,
        "quick_check": "ok",
        "foreign_key_errors": 0,
    }
    blocked = production_gate_v7.evaluate_stage13_production_gate(
        {**payload, "physical_retirement": broken}
    )
    assert blocked["production_ready"] is False
    assert blocked["status"] == "blocked"
    assert blocked["reasons"]
