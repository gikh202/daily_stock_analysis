from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from scripts.run_v6_daily import run
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


def _default_raw_result() -> dict:
    return {
        "success": True,
        "model_used": "deepseek/deepseek-v4-flash",
        "name": "Microsoft Corporation",
        "sentiment_score": 62,
        "trend_prediction": "看多",
        "operation_advice": "观望",
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": "bullish",
                    "up_probability": 65,
                    "expected_return_pct": 5.0,
                    "confidence": "中",
                    "rationale": "财报后趋势延续，但需要量价确认。",
                }
            },
        },
        "dashboard": {
            "core_conclusion": {
                "one_sentence": "中期看好，短期等待确认。",
                "position_advice": {
                    "no_position": "等待回踩后再考虑。",
                    "has_position": "持有并上移止损。",
                },
            },
            "intelligence": {
                "positive_catalysts": ["财报超预期，云业务增速强劲。"],
                "risk_alerts": ["短期 RSI 偏高，追高风险增加。"],
                "earnings_outlook": "盈利预期保持正面。",
                "sentiment_summary": "舆情中性偏多。",
                "latest_news": "近期财报成为主要催化。",
            },
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "理想买入点：95",
                    "secondary_buy": "次优买入点：92",
                    "stop_loss": "止损位：90",
                    "take_profit": "目标位：112",
                },
                "position_strategy": {
                    "suggested_position": "小仓",
                    "risk_control": "严格控制回撤。",
                },
            },
            "phase_decision": {
                "phase_context": {
                    "phase": "non_trading",
                    "is_trading_day": False,
                    "effective_daily_bar_date": "2026-01-01",
                },
                "immediate_action": "等待盘中确认，禁止追高。",
                "watch_conditions": ["开盘后站稳 MA5", "量能有效放大"],
                "next_check_time": "下一个交易日开盘后",
                "data_limitations": ["非交易日无盘中确认"],
            },
            "signal_attribution": {
                "strongest_bullish_signal": "均线多头排列",
                "strongest_bearish_signal": "短期量能不足",
            },
        },
        "analysis_summary": "趋势与基本面偏多，但执行上等待价格和量能确认。",
        "technical_analysis": "均线结构偏多。",
        "volume_analysis": "量能仍需确认。",
        "fundamental_analysis": "基本面质量较高。",
        "risk_warning": "若跌破关键支撑则趋势失效。",
    }


def _record(raw_result: dict | None = None) -> dict:
    return {
        "id": 1,
        "query_id": "q-1",
        "code": "MSFT",
        "created_at": "2026-01-01 22:30:00",
        "context_snapshot": json.dumps(_snapshot()),
        "raw_result": json.dumps(raw_result or _default_raw_result(), ensure_ascii=False),
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


def _fusion_payload(direction: str = "bullish") -> dict:
    return {
        "version": "v6-test",
        "generated_at": "2026-08-09T06:00:00",
        "market_pulse": {
            "regime": "risk_on",
            "breadth": "broad",
            "average_opportunity": 77.5,
            "average_risk": 42.0,
            "average_evidence_coverage": 0.72,
        },
        "board": [
            {
                "code": "MSFT",
                "decision": "WATCH",
                "direction": direction,
                "forecast_score": 85.7,
                "opportunity_score": 77.5,
                "quality_score": 79.1,
                "risk_score": 42.0,
                "evidence_coverage": 0.72,
                "llm_health": "fallback",
                "features": {
                    "trend": 88,
                    "momentum": 75,
                    "relative_strength": 82,
                    "volume_confirmation": 55,
                    "fundamental_quality": 84,
                    "market_regime": 80,
                },
                "trade_plan": {
                    "entry_zone": "95.0-99.0",
                    "stop_loss": 90.0,
                    "targets": [112.0, 118.0],
                    "risk_reward": 2.1,
                    "max_position_pct": 0.10,
                },
                "catalysts": ["财报超预期，云业务增速强劲。"],
                "risks": ["短期 RSI 偏高，追高风险增加。"],
                "limitations": ["催化因子尚未进入数值评分"],
            }
        ],
        "deltas": [],
        "scoreboard": {"status": "insufficient_data", "minimum_samples": 50, "horizons": []},
        "public_context": {},
        "run": {"new_signals": 1, "skipped_existing": 0, "skipped_unusable": 0, "new_outcomes": 0, "not_yet_mature": 1, "quick_check": "ok"},
    }


def test_unified_report_semantically_fuses_v4_and_v6_without_appendix() -> None:
    merged = build_unified_chinese_report(
        "# legacy v6 markdown",
        "# legacy v4 markdown",
        v6_payload=_fusion_payload(),
        v4_records=[_record()],
        report_date="2026-08-09",
    )
    assert "# AI 美股综合日报 · 2026-08-09" in merged
    assert "## 1. 今日最终总览" in merged
    assert "## 3. 标的融合分析" in merged
    assert "方向一致" in merged
    assert "V4 投研摘要" in merged
    assert "V6 确定性视角" in merged
    assert "财报超预期" in merged
    assert "融合交易计划" in merged
    assert "模型上行概率 **65%（未校准）**" in merged
    assert "## 9. V4 AI 深度分析" not in merged
    assert "legacy v4 markdown" not in merged


def test_unified_report_surfaces_direction_conflict_and_does_not_upgrade_action() -> None:
    merged = build_unified_chinese_report(
        "",
        v6_payload=_fusion_payload(direction="bearish"),
        v4_records=[_record()],
        report_date="2026-08-09",
    )
    assert "方向分歧" in merged
    assert "按风险优先原则不升级仓位" in merged
    assert "最终：观察" in merged


def test_v6_runner_generates_integrated_chinese_report_and_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("V6_FREE_SOURCE_ENRICHMENT", "false")
    stock_db = tmp_path / "stock.db"
    _create_stock_db(stock_db)
    report_dir = tmp_path / "reports"
    v6_db = tmp_path / "v6_data" / "v6_daily.db"
    v4_report = tmp_path / "report_20260101.md"
    v4_report.write_text(
        "# V4 测试日报\n\n## MSFT 深度分析\n\n- 这段原始 Markdown 不应被整段追加。\n",
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
    assert result["unified_report"]["fusion_mode"] == "structured_v4_v6"
    assert result["unified_report"]["v4_structured_records"] == 1
    assert result["unified_report"]["language"] == "zh"
    assert v6_db.is_file()
    markdown = (report_dir / "v6_daily_latest.md").read_text(encoding="utf-8")
    assert "AI 美股综合日报" in markdown
    assert "## 1. 今日最终总览" in markdown
    assert "## 3. 标的融合分析" in markdown
    assert "V4 投研摘要" in markdown
    assert "V6 确定性视角" in markdown
    assert "## 6. 预测验证看板" in markdown
    assert "## 9. V4 AI 深度分析" not in markdown
    assert "这段原始 Markdown 不应被整段追加" not in markdown
