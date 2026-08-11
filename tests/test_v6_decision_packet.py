from __future__ import annotations

from src.v6_daily.decision_contracts import (
    AssessmentVerdict,
    DECISION_PACKET_SCHEMA_VERSION,
    ExecutionStatus,
    PRE_FUSION_ASSESSMENT_SCOPE,
)
from src.v6_daily.models import V6Signal


def _signal(*, decision: str = "WATCH", trade_plan: dict | None = None) -> V6Signal:
    return V6Signal(
        analysis_history_id=101,
        query_id="q-101",
        code="MSFT",
        analysis_created_at="2026-08-11T01:00:00+00:00",
        baseline_price=505.0,
        direction="bullish",
        forecast_score=72.5,
        decision=decision,
        quality_score=81.0,
        opportunity_score=77.7,
        risk_score=47.1,
        evidence_coverage=0.82,
        market_regime="risk_on",
        market_breadth="neutral",
        model_used="deepseek/deepseek-chat",
        llm_health="healthy",
        features={"trend": 82.0, "momentum": 68.0},
        trade_plan=trade_plan or {},
        catalysts=("earnings revisions remain constructive",),
        risks=("short-term overbought condition",),
        limitations=("source-backed deterministic catalyst unavailable",),
        diagnostics={
            "engine_version": "v6.1-accuracy.1",
            "feature_adapter_version": "alpha-feature-adapter-v1",
        },
        instrument_type="STOCK",
        effective_trade_date="2026-08-10",
        horizon_forecasts={
            "5d": {"direction": "bullish", "score": 69.0},
            "10d": {"direction": "bullish", "score": 72.5},
            "20d": {"direction": "neutral", "score": 58.0},
        },
        context_features={},
    )


def _active_plan(*, action: str = "WATCH") -> dict:
    return {
        "action": action,
        "entry_zone": [502.0, 506.0],
        "stop_loss": 487.0,
        "targets": [544.0, 563.0],
        "max_position_pct": 0.05,
        "risk_reward": 2.1,
        "confirmations": ["price/volume confirmation required before entry"],
        "invalidation": ["close below 487"],
    }


def test_watch_with_active_plan_is_execution_ready_but_not_final_buy_verdict() -> None:
    signal = _signal(decision="WATCH", trade_plan=_active_plan())

    packet = signal.to_decision_packet()

    assert packet.schema_version == DECISION_PACKET_SCHEMA_VERSION
    assert packet.execution.status == ExecutionStatus.WAITING_CONFIRMATION
    assert packet.execution.has_active_plan is True
    assert packet.execution.actionable is True
    assert signal.actionable is True
    assert packet.assessment.scope == PRE_FUSION_ASSESSMENT_SCOPE
    assert packet.assessment.is_final is False
    assert packet.assessment.verdict == AssessmentVerdict.WATCH
    assert packet.assessment.worth_buying is None
    assert packet.assessment.bullish_evidence
    assert packet.assessment.bearish_evidence


def test_watch_without_complete_plan_is_not_actionable() -> None:
    signal = _signal(
        decision="WATCH",
        trade_plan={
            "action": "WATCH",
            "entry_zone": [502.0, 506.0],
            "max_position_pct": 0.05,
            # No stop/targets: downstream must not infer execution from a price range alone.
        },
    )

    packet = signal.to_decision_packet()

    assert packet.execution.status == ExecutionStatus.BLOCKED_PLAN
    assert packet.execution.has_active_plan is False
    assert packet.execution.actionable is False
    assert signal.actionable is False
    assert packet.assessment.verdict == AssessmentVerdict.WATCH
    assert packet.assessment.worth_buying is None


def test_buy_setup_requires_active_plan_before_it_is_executable() -> None:
    executable = _signal(decision="BUY_SETUP", trade_plan=_active_plan(action="BUY_SETUP"))
    blocked = _signal(
        decision="BUY_SETUP",
        trade_plan={
            "action": "BUY_SETUP",
            "entry_zone": [502.0, 506.0],
            "stop_loss": 487.0,
            "targets": [544.0],
            "max_position_pct": 0.0,
        },
    )

    executable_packet = executable.to_decision_packet()
    blocked_packet = blocked.to_decision_packet()

    assert executable_packet.execution.status == ExecutionStatus.EXECUTABLE
    assert executable_packet.execution.actionable is True
    assert executable_packet.assessment.verdict == AssessmentVerdict.BUY_BY_PLAN
    assert executable_packet.assessment.is_final is False
    assert blocked_packet.execution.status == ExecutionStatus.BLOCKED_PLAN
    assert blocked_packet.execution.actionable is False
    assert blocked_packet.assessment.verdict == AssessmentVerdict.WATCH
    assert blocked_packet.assessment.worth_buying is None


def test_wait_and_avoid_never_become_actionable_from_stale_price_levels() -> None:
    stale = _active_plan()
    wait_packet = _signal(decision="WAIT", trade_plan=stale).to_decision_packet()
    avoid_packet = _signal(decision="AVOID", trade_plan=stale).to_decision_packet()

    assert wait_packet.execution.status == ExecutionStatus.NON_ACTIONABLE
    assert wait_packet.execution.actionable is False
    assert wait_packet.assessment.verdict == AssessmentVerdict.WAIT
    assert wait_packet.assessment.worth_buying is False

    assert avoid_packet.execution.status == ExecutionStatus.BLOCKED_RISK
    assert avoid_packet.execution.actionable is False
    assert avoid_packet.assessment.verdict == AssessmentVerdict.AVOID
    assert avoid_packet.assessment.worth_buying is False


def test_packet_serialization_separates_assessment_from_execution() -> None:
    packet = _signal(decision="WATCH", trade_plan=_active_plan()).to_decision_packet().to_dict()

    assert packet["schema_version"] == DECISION_PACKET_SCHEMA_VERSION
    assert packet["identity"]["symbol"] == "MSFT"
    assert packet["assessment"]["scope"] == PRE_FUSION_ASSESSMENT_SCOPE
    assert packet["assessment"]["is_final"] is False
    assert packet["assessment"]["verdict"] == "watch"
    assert packet["assessment"]["worth_buying"] is None
    assert packet["execution"]["status"] == "waiting_confirmation"
    assert packet["execution"]["actionable"] is True
    assert packet["execution"]["entry_zone"] == [502.0, 506.0]
    assert packet["provenance"]["engine_version"] == "v6.1-accuracy.1"
