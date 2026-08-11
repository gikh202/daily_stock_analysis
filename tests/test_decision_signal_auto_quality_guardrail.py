# -*- coding: utf-8 -*-
"""Regression tests for automatic DecisionSignal data-quality guardrails."""

from __future__ import annotations

from src.analyzer import AnalysisResult
from src.services.decision_signal_extractor import build_decision_signal_payload_from_report


def _result() -> AnalysisResult:
    return AnalysisResult(
        code="AAPL",
        name="Apple",
        sentiment_score=82,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        confidence_level="高",
        report_language="zh",
        analysis_summary="趋势与量价结构偏多。",
    )


def _snapshot(level: str) -> dict:
    return {
        "analysis_context_pack_overview": {
            "data_quality": {
                "overall_score": 40 if level == "poor" else 92,
                "level": level,
            }
        }
    }


def test_fresh_auto_buy_is_downgraded_when_quality_is_poor() -> None:
    payload = build_decision_signal_payload_from_report(
        _result(),
        context_snapshot=_snapshot("poor"),
        trace_id="quality-poor",
        query_source="system",
        report_type="simple",
        profile_source="auto_default",
    )

    assert payload is not None
    assert payload["action"] == "watch"
    assert payload["action_label"] == "观望"
    assert payload["metadata"]["canonical_action"] == "buy"
    assert payload["metadata"]["final_action"] == "watch"
    assert payload["metadata"]["action_adjustment_reason"] == "data_quality_guardrail"
    assert payload["metadata"]["data_quality_level"] == "poor"
    assert payload["metadata"]["data_quality_guardrail_reason"] == "insufficient_data_quality:poor"


def test_fresh_auto_buy_is_downgraded_when_quality_contract_is_missing() -> None:
    payload = build_decision_signal_payload_from_report(
        _result(),
        context_snapshot=None,
        trace_id="quality-unknown",
        query_source="system",
        report_type="simple",
        profile_source="auto_default",
    )

    assert payload is not None
    assert payload["action"] == "watch"
    assert payload["metadata"]["data_quality_level"] == "unknown"


def test_good_quality_keeps_actionable_auto_signal() -> None:
    payload = build_decision_signal_payload_from_report(
        _result(),
        context_snapshot=_snapshot("good"),
        trace_id="quality-good",
        query_source="system",
        report_type="simple",
        profile_source="auto_default",
    )

    assert payload is not None
    assert payload["action"] == "buy"
    assert payload["metadata"]["data_quality_level"] == "high"
    assert "data_quality_guardrail_reason" not in payload["metadata"]


def test_legacy_unknown_quality_remains_reproducible() -> None:
    payload = build_decision_signal_payload_from_report(
        _result(),
        context_snapshot=None,
        trace_id="quality-legacy",
        query_source="system",
        report_type="simple",
        profile_source="legacy_unknown",
    )

    assert payload is not None
    assert payload["action"] == "buy"
    assert payload["metadata"]["data_quality_level"] == "unknown"
