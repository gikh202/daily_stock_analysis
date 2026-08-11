from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.v6_daily.legacy_archive import (
    export_legacy_archive,
    inspect_legacy_facts,
    migrate_legacy_to_normalized,
    plan_legacy_migration,
)
from src.v6_daily.normalized_schema import ensure_normalized_schema, normalized_schema_status
from src.v6_daily.production_import_guard import (
    assert_production_import_graph_clean,
    evaluate_production_import_graph,
)
from src.v6_daily.store import V6DailyStore
from src.v6_daily.versioned_store import VersionedV6DailyStore
from tests.test_v6_canonical_write_store import _signal


ENGINE = "engine-a"


def test_normalized_schema_registry_is_idempotent_and_creates_no_legacy_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v6.db"
    first = ensure_normalized_schema(path)
    second = ensure_normalized_schema(path)
    status = normalized_schema_status(path)

    assert first["status"] == "current"
    assert first["applied_now"] is True
    assert second["status"] == "current"
    assert second["applied_now"] is False
    assert second["pending_migrations"] == []
    assert status["status"] == "current"
    assert status["pending_migrations"] == []
    assert status["migration_count"] >= 1

    with sqlite3.connect(str(path)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "v6_schema_migrations" in tables
        assert "v6_forecast_runs" in tables
        assert "v6_forecast_outcomes" in tables
        assert "v6_signals" not in tables
        assert "v6_outcomes" not in tables


def test_stage11_production_import_graph_cannot_reach_retired_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    result = assert_production_import_graph_clean(root)

    assert result["status"] == "clean"
    assert result["entry_module"] == "scripts.run_v6_daily_stage11"
    assert result["forbidden_import_count"] == 0
    visited = set(result["visited_modules"])
    assert "scripts.run_v6_daily" not in visited
    assert "scripts.run_v6_daily_stage9" not in visited
    assert "scripts.run_v6_daily_stage10" not in visited
    assert "src.v6_daily.store" not in visited
    assert "src.v6_daily.versioned_store" not in visited
    assert "src.v6_daily.canonical_write_store" not in visited


def test_import_guard_blocks_direct_legacy_dependency(tmp_path: Path) -> None:
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "src" / "v6_daily").mkdir(parents=True)
    (root / "scripts" / "run_v6_daily_stage11.py").write_text(
        "from src.v6_daily.store import V6DailyStore\n",
        encoding="utf-8",
    )
    (root / "src" / "v6_daily" / "store.py").write_text(
        "class V6DailyStore: pass\n",
        encoding="utf-8",
    )

    result = evaluate_production_import_graph(root)
    assert result["status"] == "blocked"
    assert result["forbidden_import_count"] == 1
    assert result["violations"] == [
        {
            "from": "scripts.run_v6_daily_stage11",
            "to": "src.v6_daily.store",
        }
    ]


def _create_legacy_history(path: Path) -> tuple[int, dict]:
    V6DailyStore(str(path))
    store = VersionedV6DailyStore(str(path), active_engine_version=ENGINE)
    signal = _signal()
    assert store.save_signal(signal, engine_version=ENGINE) is True
    signal_id = int(store.all_signals()[0]["id"])
    assert store.save_outcome(
        signal_id=signal_id,
        horizon_days=5,
        end_trade_date="2026-08-18",
        start_price=100.0,
        end_price=104.0,
        max_high=105.0,
        min_low=99.0,
        direction="bullish",
        forecast_score=75.0,
        benchmark_spy_return_pct=1.0,
        benchmark_qqq_return_pct=1.5,
    ) is True
    return signal_id, inspect_legacy_facts(path)


def test_legacy_archive_export_is_read_only_and_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _, before = _create_legacy_history(path)
    output = tmp_path / "archive" / "legacy.json"

    summary = export_legacy_archive(path, output)
    after = inspect_legacy_facts(path)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert before["tables"] == after["tables"]
    assert summary["status"] == "exported"
    assert summary["legacy_signal_rows"] == 1
    assert summary["legacy_outcome_rows"] == 1
    assert payload["content_sha256"] == summary["content_sha256"]
    assert len(payload["tables"]["v6_signals"]["rows"]) == 1
    assert len(payload["tables"]["v6_outcomes"]["rows"]) == 1


def test_explicit_legacy_migration_is_dry_run_by_default_then_idempotent_apply(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-migrate.db"
    _, before = _create_legacy_history(path)

    plan = plan_legacy_migration(path, engine_version=ENGINE)
    dry_run = migrate_legacy_to_normalized(path, engine_version=ENGINE)
    after_dry_run = inspect_legacy_facts(path)

    assert plan["status"] == "migration_needed"
    assert plan["apply_required"] is True
    assert dry_run["mode"] == "dry_run"
    assert dry_run["applied"] is False
    assert before["tables"] == after_dry_run["tables"]

    with sqlite3.connect(str(path)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "v6_forecast_runs" not in tables

    applied = migrate_legacy_to_normalized(
        path,
        engine_version=ENGINE,
        apply=True,
        report_date="2026-08-11",
    )
    after = plan_legacy_migration(path, engine_version=ENGINE)
    legacy_after = inspect_legacy_facts(path)

    assert applied["applied"] is True
    assert applied["mode"] == "apply"
    assert after["apply_required"] is False
    assert after["status"] == "already_covered"
    assert before["tables"] == legacy_after["tables"]

    with sqlite3.connect(str(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_decision_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_execution_plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0] == 1
