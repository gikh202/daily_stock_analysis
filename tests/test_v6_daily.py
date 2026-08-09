from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.run_v6_daily import run
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.store import V6DailyStore, mature_outcomes


def _snapshot(price: float = 100.0) -> dict:
    return {
        "trend_analysis": {
            "signal_score": 82,
            "current_price": price,
            "support_levels": [95],
            "resistance_levels": [112],
        },
        "volume_price_features": {"rvol20": 1.6},
        "prediction_context": {
            "horizons": {
                "5d": {"target_return_pct": 2.5},
                "20d": {
                    "target_return_pct": 7.0,
                    "excess_vs_spy_pct": 4.0,
                    "excess_vs_qqq_pct": 3.0,
                },
                "60d": {
                    "target_return_pct": 12.0,
                    "excess_vs_spy_pct": 7.0,
                    "excess_vs_qqq_pct": 5.0,
                },
            },
            "realized_vol_20d_pct": 18.0,
        },
        "fundamental_context": {"quality_score": 88},
        "earnings_event": {"days_until_earnings": 30},
        "market_regime": {
            "regime": "risk_on",
            "market_breadth": {"breadth": "broad"},
        },
        "market_structure_context": {"support": 95, "resistance": 112},
        "realtime": {"price": price},
        "atr": 2.0,
    }


def _record(raw_result: dict | None = None) -> dict:
    return {
        "id": 1,
        "query_id": "q-1",
        "code": "MSFT",
        "created_at": "2026-01-01 22:30:00",
        "context_snapshot": json.dumps(_snapshot()),
        "raw_result": json.dumps(raw_result or {"success": True, "model_used": "deepseek/deepseek-v4-flash"}),
    }


def test_v6_numeric_forecast_is_independent_from_llm_prose() -> None:
    engine = V6DailyEngine()
    first = engine.from_analysis_record(
        _record(
            {
                "success": True,
                "model_used": "deepseek/deepseek-v4-flash",
                "dashboard": {"intelligence": {"positive_catalysts": ["LLM says huge upside"]}},
            }
        ),
        primary_model="deepseek/deepseek-v4-flash",
    )
    second = engine.from_analysis_record(
        _record(
            {
                "success": True,
                "model_used": "gemini/gemini-2.5-flash",
                "dashboard": {"intelligence": {"positive_catalysts": ["LLM says downside"]}},
            }
        ),
        primary_model="deepseek/deepseek-v4-flash",
    )

    assert first is not None and second is not None
    assert first.forecast_score == second.forecast_score
    assert first.direction == second.direction
    assert first.opportunity_score == second.opportunity_score
    assert first.risk_score == second.risk_score
    assert first.llm_health == "healthy"
    assert second.llm_health == "fallback"
    assert first.evidence_coverage > 0.5


def _create_stock_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE analysis_history (
                id INTEGER PRIMARY KEY,
                query_id TEXT,
                code TEXT,
                context_snapshot TEXT,
                raw_result TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE stock_daily (
                code TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL
            )
            """
        )
        record = _record()
        conn.execute(
            "INSERT INTO analysis_history(id,query_id,code,context_snapshot,raw_result,created_at) VALUES (?,?,?,?,?,?)",
            (
                record["id"],
                record["query_id"],
                record["code"],
                record["context_snapshot"],
                record["raw_result"],
                record["created_at"],
            ),
        )
        for day in range(1, 21):
            close = 100.0 + day * 0.5
            conn.execute(
                "INSERT INTO stock_daily(code,date,open,high,low,close) VALUES (?,?,?,?,?,?)",
                ("MSFT", f"2026-02-{day:02d}", close - 0.2, close + 0.5, close - 0.5, close),
            )
        conn.commit()
    finally:
        conn.close()


def test_v6_store_matures_by_future_trading_bars(tmp_path: Path) -> None:
    stock_db = tmp_path / "stock.db"
    _create_stock_db(stock_db)
    store = V6DailyStore(str(tmp_path / "v6.db"))
    signal = V6DailyEngine().from_analysis_record(_record())
    assert signal is not None
    assert store.save_signal(signal, engine_version=V6DailyEngine.version)

    stats = mature_outcomes(store, str(stock_db))
    assert stats["evaluated"] == 3
    assert stats["not_yet_mature"] == 0
    scorecard = store.scoreboard(min_samples=3)
    assert scorecard["status"] == "measurable"
    assert {item["horizon_days"] for item in scorecard["horizons"]} == {5, 10, 20}
    assert all(item["directional_hit_rate_pct"] == 100.0 for item in scorecard["horizons"])


def test_v6_runner_generates_daily_report_and_database(tmp_path: Path) -> None:
    stock_db = tmp_path / "stock.db"
    _create_stock_db(stock_db)
    report_dir = tmp_path / "reports"
    v6_db = tmp_path / "v6_data" / "v6_daily.db"

    result = run(
        stock_db_path=str(stock_db),
        v6_db_path=str(v6_db),
        report_dir=str(report_dir),
        limit=100,
        min_samples=3,
        primary_model="deepseek/deepseek-v4-flash",
        notify=False,
    )

    assert result["run"]["new_signals"] == 1
    assert result["run"]["new_outcomes"] == 3
    assert v6_db.is_file()
    markdown = (report_dir / "v6_daily_latest.md").read_text(encoding="utf-8")
    assert "V6 AI 美股日报" in markdown
    assert "Opportunity Ranking" in markdown
    assert "Setup Cards" in markdown
    assert "Prediction Scoreboard" in markdown
    assert "LLM prose has zero direct numeric influence" in markdown
