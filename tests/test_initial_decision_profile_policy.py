from __future__ import annotations

from src.analyzer import AnalysisResult
from src.services.decision_signal_extractor import build_decision_signal_payload_from_report


def _snapshot(level: str) -> dict:
    return {
        "market_phase_summary": {"phase": "postmarket"},
        "analysis_context_pack_overview": {
            "data_quality": {
                "overall_score": 90 if level == "good" else 35,
                "level": level,
            }
        },
    }


def _result(*, with_price_plan: bool = True) -> AnalysisResult:
    result = AnalysisResult(
        code="AAPL",
        name="Apple",
        sentiment_score=82,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        confidence_level="高",
        report_language="zh",
        analysis_summary="趋势、量价与基本面证据支持当前结论。",
    )
    if with_price_plan:
        result.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "100",
                    "secondary_buy": "102",
                    "stop_loss": "95",
                    "take_profit": "120",
                }
            }
        }
    else:
        result.dashboard = {}
    return result


def _build(result: AnalysisResult, *, level: str = "good"):
    return build_decision_signal_payload_from_report(
        result,
        context_snapshot=_snapshot(level),
        source_report_id=101,
        trace_id="initial-policy",
        query_source="system",
        report_type="full",
        profile_source="auto_default",
    )


def test_persisted_initial_signal_uses_shared_profile_policy() -> None:
    payload = _build(_result(), level="good")

    assert payload is not None
    assert payload["action"] == "buy"
    metadata = payload["metadata"]
    assert metadata["profile_policy_version"] == "decision-profile-v1"
    assert metadata["signal_generation_version"] == "decision-profile-initial-v2"
    assert metadata["guardrail_result"]["passed"] is True
    assert metadata["guardrail_result"]["final_action"] == "buy"
    assert metadata["scoring_breakdown"]["policy"] == "minimal_deterministic"


def test_persisted_initial_signal_uses_same_data_quality_rule_as_reassessment() -> None:
    payload = _build(_result(), level="poor")

    assert payload is not None
    assert payload["action"] == "watch"
    metadata = payload["metadata"]
    assert "insufficient_data_quality" in metadata["guardrail_result"]["violations"]
    assert metadata["data_quality_guardrail_reason"] == "insufficient_data_quality:poor"
    assert metadata["action_adjustment_reason"] == "data_quality_guardrail"


def test_persisted_initial_buy_without_stop_or_invalidation_is_downgraded() -> None:
    payload = _build(_result(with_price_plan=False), level="good")

    assert payload is not None
    assert payload["action"] == "watch"
    metadata = payload["metadata"]
    assert "missing_invalidation_or_stop_loss" in metadata["guardrail_result"]["violations"]
    assert metadata["action_adjustment_reason"] == "decision_profile_policy"
    assert metadata["guardrail_reason"].startswith("decision_profile_policy:")


def test_persisted_initial_signal_blocks_contradictory_price_relationships() -> None:
    result = _result()
    result.dashboard = {
        "battle_plan": {
            "sniper_points": {
                "ideal_buy": "100",
                "secondary_buy": "102",
                "stop_loss": "110",
                "take_profit": "105",
            }
        }
    }

    payload = _build(result, level="good")

    assert payload is None
