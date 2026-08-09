from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from scripts.run_v6_daily import run
from src.notification_sender.email_sender import EmailSender
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.free_sources import source_status
from src.v6_daily.store import V6DailyStore, mature_outcomes
from src.v6_daily.unified_report import build_unified_chinese_report


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


def test_free_source_enrichment_is_safe_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv("V6_FREE_SOURCE_ENRICHMENT", "true")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    status = source_status()
    assert status["enabled"] is True
    assert status["sec"]["configured"] is False
    assert status["fred"]["configured"] is False


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
    assert scorecard["status"] == "insufficient_data"
    assert {item["horizon_days"] for item in scorecard["horizons"]} == {5, 10, 20}
    assert all(item["samples"] == 1 for item in scorecard["horizons"])
    assert all(item["directional_hit_rate_pct"] == 100.0 for item in scorecard["horizons"])


def test_buy_and_avoid_metrics_do_not_reuse_forecast_direction(tmp_path: Path) -> None:
    store = V6DailyStore(str(tmp_path / "v6_metrics.db"))
    base = V6DailyEngine().from_analysis_record(_record())
    assert base is not None

    buy = replace(
        base,
        analysis_history_id=1,
        query_id="buy",
        code="BUY",
        decision="BUY_SETUP",
        direction="bearish",
    )
    avoid = replace(
        base,
        analysis_history_id=2,
        query_id="avoid",
        code="AVOID",
        decision="AVOID",
        direction="bullish",
    )
    assert store.save_signal(buy, engine_version=V6DailyEngine.version)
    assert store.save_signal(avoid, engine_version=V6DailyEngine.version)

    rows = store.all_signals()
    by_code = {row["code"]: int(row["id"]) for row in rows}
    store.save_outcome(
        signal_id=by_code["BUY"],
        horizon_days=5,
        end_trade_date="2026-02-05",
        start_price=100.0,
        end_price=105.0,
        max_high=106.0,
        min_low=99.0,
        direction="bearish",
    )
    store.save_outcome(
        signal_id=by_code["AVOID"],
        horizon_days=5,
        end_trade_date="2026-02-05",
        start_price=100.0,
        end_price=90.0,
        max_high=101.0,
        min_low=89.0,
        direction="bullish",
    )

    horizon = store.scoreboard(min_samples=3)["horizons"][0]
    assert horizon["directional_hit_rate_pct"] == 0.0
    assert horizon["buy_setup_hit_rate_pct"] == 100.0
    assert horizon["avoidance_hit_rate_pct"] == 100.0
    assert horizon["false_avoid_rate_pct"] == 0.0
    assert horizon["avg_avoided_return_pct"] == -10.0


def test_unified_report_translates_v6_and_keeps_full_v4_detail() -> None:
    v6 = """# V6 AI 美股日报 · 2026-08-09

## 1. Market Pulse

- Regime: **risk_on**

## 3. Opportunity Ranking

| Rank | Symbol | Decision | Direction | Forecast | Opportunity | Quality | Risk | Evidence | LLM |
|---:|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | MSFT | WATCH | bullish | 85.7 | 77.5 | 79.1 | 59.3 | 72% | fallback |
"""
    v4 = """# 🎯 2026-08-09 决策仪表盘

## ⚪ Microsoft Corporation (MSFT)

### 📰 重要信息速览

**✨ 利好催化**: 财报超预期。
"""
    merged = build_unified_chinese_report(v6, v4)
    assert "## 1. 市场脉搏" in merged
    assert "## 3. 机会排名" in merged
    assert "| 1 | MSFT | 观察 | 看多 |" in merged
    assert "## 9. V4 AI 深度分析" in merged
    assert "### 📰 重要信息速览" in merged
    assert "财报超预期" in merged
    assert "# 🎯 2026-08-09 决策仪表盘" not in merged


def test_upstream_v4_email_is_suppressed_but_final_v6_is_allowed(monkeypatch) -> None:
    class DummyConfig:
        email_sender = "sender@gmail.com"
        email_sender_name = "日报"
        email_password = "secret"
        email_receivers = ["receiver@gmail.com"]
        stock_email_groups = []

    sender = EmailSender(DummyConfig())
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("MERGE_EMAIL_NOTIFICATION", "true")
    monkeypatch.delenv("V6_UNIFIED_EMAIL_FINAL", raising=False)
    assert sender._is_email_configured() is False

    monkeypatch.setenv("V6_UNIFIED_EMAIL_FINAL", "true")
    assert sender._is_email_configured() is True


def test_v6_runner_generates_chinese_unified_report_and_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("V6_FREE_SOURCE_ENRICHMENT", "false")
    stock_db = tmp_path / "stock.db"
    _create_stock_db(stock_db)
    report_dir = tmp_path / "reports"
    v6_db = tmp_path / "v6_data" / "v6_daily.db"
    v4_report = tmp_path / "report_20260101.md"
    v4_report.write_text(
        "# V4 测试日报\n\n## MSFT 深度分析\n\n- V4 原始详情保留。\n",
        encoding="utf-8",
    )

    result = run(
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
    assert result["unified_report"]["v4_merged"] is True
    assert result["unified_report"]["language"] == "zh"
    assert v6_db.is_file()
    markdown = (report_dir / "v6_daily_latest.md").read_text(encoding="utf-8")
    assert "V6 AI 美股日报" in markdown
    assert "## 1. 市场脉搏" in markdown
    assert "## 3. 机会排名" in markdown
    assert "## 4. 交易计划卡" in markdown
    assert "## 6. 预测验证看板" in markdown
    assert "## 7. 免费公共数据" in markdown
    assert "LLM 文本对 V6 数值评分没有直接影响" in markdown
    assert "## 9. V4 AI 深度分析" in markdown
    assert "V4 原始详情保留" in markdown
