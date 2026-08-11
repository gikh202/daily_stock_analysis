from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.run_v6_daily_stage10 import run as run_stage10
from src.v6_daily.production_gate import assert_stage10_production_gate
from tests.test_v6_daily import _create_stock_db


def test_stage10_entrypoint_runs_without_creating_legacy_fact_tables(
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
        "# V4 测试日报\n\n## MSFT 深度分析\n\n- Stage 10 integration fixture.\n",
        encoding="utf-8",
    )

    result = run_stage10(
        stock_db_path=str(stock_db),
        v6_db_path=str(v6_db),
        report_dir=str(report_dir),
        limit=100,
        min_samples=3,
        primary_model="deepseek/deepseek-v4-flash",
        notify=False,
        v4_report_path=str(v4_report),
    )

    assert result["run"]["new_signals"] == 1
    assert result["run"]["new_outcomes"] == 3
    assert result["write_path"]["mode"] == "normalized_only_no_legacy_projection"
    assert result["write_path"]["legacy_projection_enabled"] is False
    assert result["write_path"]["legacy_projection_writes"] == 0
    assert result["write_path"]["automatic_legacy_bootstrap"] is False
    assert result["legacy_write_guard"]["status"] == "unchanged"
    assert result["legacy_write_guard"]["legacy_writes_detected"] is False
    assert result["legacy_retirement"]["legacy_projection_enabled"] is False
    assert result["legacy_retirement"]["legacy_consumer_count"] == 0

    with sqlite3.connect(str(v6_db)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "v6_signals" not in tables
        assert "v6_outcomes" not in tables
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v6_forecast_outcomes").fetchone()[0] == 3

    run_payload = json.loads(
        (report_dir / "v6_run.json").read_text(encoding="utf-8")
    )
    payload = json.loads(
        (report_dir / "v6_daily_latest.json").read_text(encoding="utf-8")
    )
    assert run_payload["stage10_entrypoint"] == "v6-stage10-legacy-projection-shutdown-v1"
    assert run_payload["write_path"]["identity_source"] == "normalized_sequence_only"
    assert run_payload["legacy_write_guard"]["fact_tables_unchanged"] is True
    assert payload["legacy_write_guard"]["legacy_projection_writes"] == 0
    assert payload["legacy_retirement"]["legacy_projection_policy"] == (
        "historical_read_only_explicit_migration_source"
    )

    gate = assert_stage10_production_gate(run_payload)
    assert gate["production_ready"] is True
    assert gate["legacy_projection_enabled"] is False
    assert gate["legacy_projection_writes"] == 0
    assert gate["legacy_fact_tables_unchanged"] is True


def test_stage10_active_writer_and_entrypoint_have_no_legacy_fact_sql() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "src/v6_daily/normalized_write_store.py",
        root / "scripts/run_v6_daily_stage10.py",
    )
    banned = (
        "INSERT INTO v6_signals",
        "INSERT INTO v6_outcomes",
        "UPDATE v6_signals",
        "UPDATE v6_outcomes",
        "DELETE FROM v6_signals",
        "DELETE FROM v6_outcomes",
        "MAX(id) FROM v6_signals",
        "MAX(id) FROM v6_outcomes",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, (path, token)
