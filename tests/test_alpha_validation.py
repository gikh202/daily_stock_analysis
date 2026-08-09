from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_alpha_backtest import _reset_backtest_database
from src.alpha_engine.shadow_store import AlphaShadowStore
from src.alpha_engine.validation import (
    build_validation_summary,
    render_validation_markdown,
    write_validation_report,
)


def _seed_signal(
    store: AlphaShadowStore,
    *,
    history_id: int,
    code: str,
    created_at: str,
    decision: str,
    opportunity_score: float,
    confidence: float,
    return_pct: float,
) -> None:
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO alpha_signals(
                analysis_history_id, query_id, code,
                analysis_created_at, shadow_created_at,
                engine_version, feature_version,
                quality_score, opportunity_score, risk_score,
                confidence, decision, baseline_price, market_regime,
                features_json, trade_plan_json, reasons_json,
                limitations_json, diagnostics_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                history_id,
                f"q-{history_id}",
                code,
                created_at,
                created_at,
                "test-engine",
                "test-features",
                70.0,
                opportunity_score,
                30.0,
                confidence,
                decision,
                100.0,
                "range",
                "{}",
                "{}",
                "[]",
                "[]",
                "{}",
            ),
        )
        signal_id = int(cursor.lastrowid)

    end_price = 100.0 * (1.0 + return_pct / 100.0)
    store.save_outcome(
        signal_id=signal_id,
        horizon_days=5,
        end_trade_date=f"2026-02-{history_id + 10:02d}",
        start_price=100.0,
        end_price=end_price,
        max_high=max(100.0, end_price),
        min_low=min(100.0, end_price),
        decision=decision,
    )


def _seed_validation_dataset(store: AlphaShadowStore) -> None:
    rows = [
        (1, "AAA", "2026-01-01T00:00:00+00:00", "BUY_SETUP", 90.0, 0.80, 5.0),
        (2, "BBB", "2026-01-02T00:00:00+00:00", "BUY_SETUP", 80.0, 0.75, 4.0),
        (3, "CCC", "2026-01-03T00:00:00+00:00", "BUY_SETUP", 70.0, 0.70, -1.0),
        (4, "DDD", "2026-01-04T00:00:00+00:00", "AVOID", 30.0, 0.70, -1.0),
        (5, "EEE", "2026-01-05T00:00:00+00:00", "AVOID", 20.0, 0.75, -2.0),
        (6, "FFF", "2026-01-06T00:00:00+00:00", "AVOID", 10.0, 0.80, -4.0),
    ]
    for row in rows:
        _seed_signal(
            store,
            history_id=row[0],
            code=row[1],
            created_at=row[2],
            decision=row[3],
            opportunity_score=row[4],
            confidence=row[5],
            return_pct=row[6],
        )


def test_validation_summary_reports_rank_quality_and_risk_proxies(tmp_path: Path) -> None:
    store = AlphaShadowStore(str(tmp_path / "alpha_shadow.db"))
    _seed_validation_dataset(store)

    summary = build_validation_summary(store, min_samples=5, primary_horizon=5)

    assert summary["coverage"]["signals"] == 6
    assert summary["coverage"]["evaluated_signals"] == 6
    assert summary["research_gate"]["status"] == "research_pass"
    assert summary["research_gate"]["production_effect"] == "none"

    horizon = summary["horizons"][0]
    assert horizon["horizon_days"] == 5
    assert horizon["directional_hit_rate_pct"] == pytest.approx(83.33, abs=0.01)
    assert horizon["avg_strategy_return_pct"] == pytest.approx(2.5, abs=0.001)
    assert horizon["profit_factor"] == pytest.approx(16.0, abs=0.001)
    assert horizon["opportunity_ic_spearman"] > 0.9
    assert horizon["sequence_max_drawdown_proxy_pct"] > 0


def test_validation_report_writes_machine_and_trader_views(tmp_path: Path) -> None:
    store = AlphaShadowStore(str(tmp_path / "alpha_shadow.db"))
    _seed_validation_dataset(store)
    report_dir = tmp_path / "reports"

    summary = write_validation_report(
        store,
        report_dir,
        min_samples=5,
        primary_horizon=5,
    )

    json_path = report_dir / "validation_latest.json"
    md_path = report_dir / "validation_latest.md"
    assert json_path.is_file()
    assert md_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["validation_version"] == summary["validation_version"]

    markdown = md_path.read_text(encoding="utf-8")
    assert "Research gate: **research_pass**" in markdown
    assert "Profit Factor" in markdown
    assert "Sharpe Proxy" in markdown
    assert "DD Proxy" in markdown
    assert "shadow-only" in markdown


def test_validation_stays_insufficient_until_sample_floor_is_met(tmp_path: Path) -> None:
    store = AlphaShadowStore(str(tmp_path / "alpha_shadow.db"))
    _seed_validation_dataset(store)

    summary = build_validation_summary(store, min_samples=20, primary_horizon=5)

    assert summary["research_gate"]["status"] == "insufficient_data"
    assert summary["research_gate"]["checks"]["sample_size"] is False
    assert summary["horizons"][0]["mature"] is False


def test_backtest_reset_refuses_shadow_database_name(tmp_path: Path) -> None:
    protected = tmp_path / "alpha_shadow.db"
    protected.write_text("do-not-delete", encoding="utf-8")

    with pytest.raises(ValueError, match="backtest"):
        _reset_backtest_database(protected)

    assert protected.read_text(encoding="utf-8") == "do-not-delete"


def test_render_validation_markdown_handles_empty_database(tmp_path: Path) -> None:
    store = AlphaShadowStore(str(tmp_path / "alpha_shadow.db"))
    summary = build_validation_summary(store)
    markdown = render_validation_markdown(summary)

    assert summary["research_gate"]["status"] == "insufficient_data"
    assert "| - | 0 | N/A" in markdown
