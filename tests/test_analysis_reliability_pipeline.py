# -*- coding: utf-8 -*-
"""End-to-end regression coverage for analysis reliability hardening."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline
from src.data_quality_decision_guardrail import apply_data_quality_decision_guardrail


def test_poor_data_quality_downgrades_action_before_persistence() -> None:
    result = AnalysisResult(
        code="AAPL",
        name="Apple",
        sentiment_score=82,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        confidence_level="高",
        report_language="zh",
        analysis_summary="趋势偏多。",
        dashboard={"phase_decision": {}},
    )
    result.action = "buy"
    result.action_label = "买入"

    overview = {
        "blocks": [
            {
                "key": "quote",
                "label": "行情",
                "status": "fetch_failed",
                "source": "test",
                "warnings": [],
                "missing_reasons": ["provider_unavailable"],
            },
            {
                "key": "daily_bars",
                "label": "日线",
                "status": "available",
                "source": "test",
                "warnings": [],
                "missing_reasons": [],
            },
            {
                "key": "technical",
                "label": "技术",
                "status": "available",
                "source": "local",
                "warnings": [],
                "missing_reasons": [],
            },
        ],
        "data_quality": {
            "overall_score": 40,
            "level": "poor",
            "limitations": ["quote: fetch_failed"],
        },
    }

    adjustments = apply_data_quality_decision_guardrail(
        result,
        analysis_context_pack_overview=overview,
        report_language="zh",
    )

    assert "action_downgraded_poor_data_quality" in adjustments
    assert result.action == "watch"
    assert result.action_label == "观望"
    assert result.operation_advice == "观望"
    assert result.decision_type == "hold"
    assert (
        result.dashboard["decision_stability"]["data_quality_guardrail"]["reason"]
        == "insufficient_data_quality:poor"
    )


def test_initial_stock_sync_prefetches_enough_history_for_real_ma60() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = MagicMock()
    pipeline.db = MagicMock()
    pipeline._resolve_resume_target_date = MagicMock(return_value=date(2026, 8, 11))

    pipeline.fetcher_manager.get_stock_name.return_value = "Apple"
    pipeline.fetcher_manager.get_daily_data.return_value = (
        pd.DataFrame([{"date": "2026-08-11", "close": 100.0}]),
        "test-provider",
    )
    pipeline.db.has_today_data.return_value = False
    pipeline.db.save_daily_data.return_value = 1

    success, error = pipeline.fetch_and_save_stock_data("AAPL")

    assert success is True
    assert error is None
    pipeline.fetcher_manager.get_daily_data.assert_called_once_with("AAPL", days=60)
    pipeline.db.save_daily_data.assert_called_once()
