from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.run_v6_daily_stage11 import run as run_stage11
from src.v6_daily.production_gate_v5 import assert_stage11_production_gate
from tests.test_v6_daily import _create_stock_db


def test_stage11_native_runner_has_no_legacy_runtime_or_fact_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("V6_FREE_SOURCE_ENRICHMENT", "false")
    monkeypatch.setenv("V6_ACCURACY_LAB_PROMOTION_MIN_SAMPLES", "3")
    monkeypatch.setenv("V6_ACCURACY_LAB_MAX_HOLDING_BARS", "20")

    stock_db = tmp_path / "stock.db"
    _create_stock_db(stock_db)
    report_dir = tmp_path / "reports"
    v6_db = tmp_path / "v6_data" / "v6_daily.db"
    v4_report = tmp_path / "report_20260101.md"
    v4_report.write_text(
        "# V4 测试日报\n\n## MSFT 深度分析\n\n- Stage 11 native integration fixture.\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]

    result = run_stage11(
        stock_db_path=str(stock_db),
        v6_db_path=str(v6_db),
        report_dir=str(report_dir),
        limit=100,
        min_samples=3,
        primary_model="deepseek/deepseek-v4-flash",
        notify=False,
        v4_report_path=str(v4_report),
        repo_root=repo_root,
    )

    assert result["run"]["new_signals"] == 1
    assert result["run"]["new_outcomes"] == 4
    assert result["run"]["production_runner"] == "native_normalized_stage11"
    assert result["write_path"]["mode"] == "normalized_only_no_legacy_projection"
    assert result["write_path"]["legacy_projection_enabled"] is False
    assert result["write_path"]["legacy_projection_writes"] == 0
    assert result["write_path"]["automatic_legacy_bootstrap"] is False
    assert result["legacy_write_guard"]["status"] == "unchanged"
    assert result["legacy_write_guard"]["legacy_writes_detected"] is False
    assert result["legacy_retirement"]["legacy_consumer_count"] == 0
    assert result["schema_registry"]["status"] == "current"
    assert result["schema_registry"]["pending_migrations"] == []
    assert result["production_import_guard"]["status"] == "clean"
    assert result["production_import_guard"]["forbidden_import_count"] == 0
    assert result["legacy_archival"]["migration_policy"] == "explicit_cli_only"
    assert result["legacy_archival"]["archive_policy"] == "explicit_cli_only"
    assert result["legacy_archival"]["automatic_migration"] is False
    assert result["legacy_archival"]["automatic_archive"] is False
    assert result["legacy_archival"]["drop_legacy_tables"] is False

    with sqlite3.connect(str(v6_db)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "v6_schema_migrations" in tables
        assert "v6_signals" not in tables
        assert "v6_outcomes" not in tables
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0] == 4
        horizons = {
            int(row[0])
            for row in conn.execute(
                "SELECT horizon_days FROM v6_forecast_outcomes ORDER BY horizon_days"
            ).fetchall()
        }
        assert horizons == {1, 5, 10, 20}

    run_payload = json.loads(
        (report_dir / "v6_run.json").read_text(encoding="utf-8")
    )
    payload = json.loads(
        (report_dir / "v6_daily_latest.json").read_text(encoding="utf-8")
    )
    lab_payload = json.loads(
        (report_dir / "v6_accuracy_lab.json").read_text(encoding="utf-8")
    )

    assert run_payload["stage11_entrypoint"] == "v6-stage11-legacy-schema-archival-v1"
    assert run_payload["read_cutover"]["selected_source"] == "normalized_v6_tables"
    assert run_payload["read_cutover"]["legacy_reference_used"] is False
    assert run_payload["normalized_storage"]["source_mode"] == "normalized_only"
    assert run_payload["normalized_storage"]["legacy_reference_used"] is False
    assert run_payload["schema_registry"]["status"] == "current"
    assert run_payload["production_import_guard"]["forbidden_import_count"] == 0
    assert payload["legacy_write_guard"]["fact_tables_unchanged"] is True
    assert payload["legacy_retirement"]["legacy_projection_enabled"] is False
    assert lab_payload["source"]["mode"] == "normalized_only"
    assert lab_payload["source"]["legacy_signal_reads"] == 0
    assert lab_payload["source"]["legacy_outcome_reads"] == 0

    gate = assert_stage11_production_gate(run_payload)
    assert gate["production_ready"] is True
    assert gate["cache_persist_allowed"] is True
    assert gate["notification_allowed"] is True
    assert gate["production_import_graph_status"] == "clean"
    assert gate["forbidden_import_count"] == 0
    assert gate["schema_registry_status"] == "current"


def test_stage11_entrypoint_and_runtime_do_not_import_retired_runtime_modules() -> None:
    root = Path(__file__).resolve().parents[1]
    production_runner = root / "src/v6_daily/production_runner.py"
    paths = (
        root / "scripts/run_v6_daily_stage11.py",
        production_runner,
        root / "src/v6_daily/production_write_store.py",
        root / "src/v6_daily/production_read_store.py",
        root / "src/v6_daily/production_outcomes.py",
        root / "src/v6_daily/production_report.py",
        root / "src/v6_daily/production_cutover.py",
    )
    assert "V6DailyEngine(history_db_path=v6_db_path)" in production_runner.read_text(
        encoding="utf-8"
    )
    banned = (
        "from scripts.run_v6_daily import",
        "import scripts.run_v6_daily",
        "from scripts.run_v6_daily_stage9 import",
        "from scripts.run_v6_daily_stage10 import",
        "from .store import",
        "from src.v6_daily.store import",
        "from .versioned_store import",
        "from .canonical_write_store import",
        "from .normalized_write_store import",
        "from .normalized_read_store import",
        "from .normalized_persistence import",
        "from .read_cutover import",
        "INSERT INTO v6_signals",
        "INSERT INTO v6_outcomes",
        "UPDATE v6_signals",
        "UPDATE v6_outcomes",
        "DELETE FROM v6_signals",
        "DELETE FROM v6_outcomes",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, (path, token)
