from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple


DECISION_PACKET_SCHEMA_VERSION = "decision-packet-v1"


class ExecutionStatus(str, Enum):
    EXECUTABLE = "executable"
    WAITING_CONFIRMATION = "waiting_confirmation"
    BLOCKED_RISK = "blocked_risk"
    BLOCKED_PLAN = "blocked_plan"
    NON_ACTIONABLE = "non_actionable"


class AssessmentVerdict(str, Enum):
    BUY_BY_PLAN = "buy_by_plan"
    CONDITIONAL_BUY = "conditional_buy"
    WATCH = "watch"
    WAIT = "wait"
    AVOID = "avoid"
    UNKNOWN = "unknown"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text_tuple(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _price_range(value: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low = _finite(value[0])
    high = _finite(value[1])
    if low is None or high is None or low <= 0 or high <= 0 or low > high:
        return None
    return (low, high)


@dataclass(frozen=True)
class ExecutionPlanContract:
    status: ExecutionStatus
    action: str
    entry_zone: Optional[Tuple[float, float]] = None
    stop_loss: Optional[float] = None
    targets: Tuple[float, ...] = ()
    max_position_pct: float = 0.0
    risk_reward: Optional[float] = None
    confirmations: Tuple[str, ...] = ()
    invalidations: Tuple[str, ...] = ()

    @property
    def has_active_plan(self) -> bool:
        return bool(
            self.entry_zone
            and self.stop_loss is not None
            and self.targets
            and self.max_position_pct > 0
        )

    @property
    def actionable(self) -> bool:
        return self.has_active_plan and self.status in {
            ExecutionStatus.EXECUTABLE,
            ExecutionStatus.WAITING_CONFIRMATION,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "action": self.action,
            "entry_zone": list(self.entry_zone) if self.entry_zone else None,
            "stop_loss": self.stop_loss,
            "targets": list(self.targets),
            "max_position_pct": self.max_position_pct,
            "risk_reward": self.risk_reward,
            "confirmations": list(self.confirmations),
            "invalidations": list(self.invalidations),
            "has_active_plan": self.has_active_plan,
            "actionable": self.actionable,
        }


@dataclass(frozen=True)
class DecisionAssessment:
    verdict: AssessmentVerdict
    worth_buying: Optional[bool]
    rationale: str
    bullish_evidence: Tuple[str, ...] = ()
    bearish_evidence: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "worth_buying": self.worth_buying,
            "rationale": self.rationale,
            "bullish_evidence": list(self.bullish_evidence),
            "bearish_evidence": list(self.bearish_evidence),
        }


@dataclass(frozen=True)
class DecisionPacket:
    symbol: str
    instrument_type: str
    effective_trade_date: Optional[str]
    direction: str
    forecast_score: Optional[float]
    horizon_forecasts: Mapping[str, Mapping[str, Any]]
    quality_score: Optional[float]
    opportunity_score: Optional[float]
    risk_score: Optional[float]
    evidence_coverage: float
    assessment: DecisionAssessment
    execution: ExecutionPlanContract
    catalysts: Tuple[str, ...]
    risks: Tuple[str, ...]
    limitations: Tuple[str, ...]
    engine_version: str
    feature_adapter_version: Optional[str]
    schema_version: str = DECISION_PACKET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": {
                "symbol": self.symbol,
                "instrument_type": self.instrument_type,
                "effective_trade_date": self.effective_trade_date,
            },
            "forecast": {
                "direction": self.direction,
                "score": self.forecast_score,
                "horizons": {
                    str(key): dict(value)
                    for key, value in self.horizon_forecasts.items()
                },
            },
            "scores": {
                "quality": self.quality_score,
                "opportunity": self.opportunity_score,
                "risk": self.risk_score,
                "evidence_coverage": self.evidence_coverage,
            },
            "assessment": self.assessment.to_dict(),
            "execution": self.execution.to_dict(),
            "evidence": {
                "catalysts": list(self.catalysts),
                "risks": list(self.risks),
                "limitations": list(self.limitations),
            },
            "provenance": {
                "engine_version": self.engine_version,
                "feature_adapter_version": self.feature_adapter_version,
            },
        }


def build_execution_plan(decision: Any, trade_plan: Mapping[str, Any]) -> ExecutionPlanContract:
    normalized = str(decision or "WAIT").strip().upper()
    action = str(trade_plan.get("action") or normalized).strip().upper()
    entry_zone = _price_range(trade_plan.get("entry_zone"))
    stop_loss = _finite(trade_plan.get("stop_loss"))
    targets = tuple(
        value
        for value in (_finite(item) for item in (trade_plan.get("targets") or ()))
        if value is not None and value > 0
    )
    max_position = _finite(trade_plan.get("max_position_pct")) or 0.0
    risk_reward = _finite(trade_plan.get("risk_reward"))

    has_active_plan = bool(
        entry_zone
        and stop_loss is not None
        and targets
        and max_position > 0
    )
    if normalized == "AVOID":
        status = ExecutionStatus.BLOCKED_RISK
    elif normalized == "WAIT":
        status = ExecutionStatus.NON_ACTIONABLE
    elif not has_active_plan:
        status = ExecutionStatus.BLOCKED_PLAN
    elif normalized == "BUY_SETUP":
        status = ExecutionStatus.EXECUTABLE
    elif normalized == "WATCH":
        status = ExecutionStatus.WAITING_CONFIRMATION
    else:
        status = ExecutionStatus.NON_ACTIONABLE

    return ExecutionPlanContract(
        status=status,
        action=action,
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        targets=targets,
        max_position_pct=max(0.0, max_position),
        risk_reward=risk_reward,
        confirmations=_text_tuple(trade_plan.get("confirmations")),
        invalidations=_text_tuple(trade_plan.get("invalidation")),
    )


def build_assessment(
    decision: Any,
    execution: ExecutionPlanContract,
    *,
    catalysts: Sequence[str] = (),
    risks: Sequence[str] = (),
) -> DecisionAssessment:
    normalized = str(decision or "").strip().upper()
    if normalized == "BUY_SETUP" and execution.actionable:
        verdict = AssessmentVerdict.BUY_BY_PLAN
        worth_buying: Optional[bool] = True
        rationale = "deterministic decision and active execution plan both permit buying by plan"
    elif normalized == "BUY_SETUP":
        verdict = AssessmentVerdict.WATCH
        worth_buying = None
        rationale = "buy setup was not promoted to execution because the structured trade plan is incomplete or disabled"
    elif normalized == "WATCH" and execution.actionable:
        verdict = AssessmentVerdict.CONDITIONAL_BUY
        worth_buying = True
        rationale = "setup remains conditional: active plan exists but confirmation is still required"
    elif normalized == "WATCH":
        verdict = AssessmentVerdict.WATCH
        worth_buying = None
        rationale = "watch state has no active executable plan"
    elif normalized == "WAIT":
        verdict = AssessmentVerdict.WAIT
        worth_buying = False
        rationale = "deterministic decision is waiting and does not authorize a new position"
    elif normalized == "AVOID":
        verdict = AssessmentVerdict.AVOID
        worth_buying = False
        rationale = "risk gate currently blocks a new position"
    else:
        verdict = AssessmentVerdict.UNKNOWN
        worth_buying = None
        rationale = "decision state is not recognized by the current decision-packet contract"

    return DecisionAssessment(
        verdict=verdict,
        worth_buying=worth_buying,
        rationale=rationale,
        bullish_evidence=_text_tuple(catalysts),
        bearish_evidence=_text_tuple(risks),
    )


def build_decision_packet(signal: Any) -> DecisionPacket:
    diagnostics = getattr(signal, "diagnostics", {}) or {}
    trade_plan = getattr(signal, "trade_plan", {}) or {}
    execution = build_execution_plan(getattr(signal, "decision", None), trade_plan)
    catalysts = _text_tuple(getattr(signal, "catalysts", ()))
    risks = _text_tuple(getattr(signal, "risks", ()))
    assessment = build_assessment(
        getattr(signal, "decision", None),
        execution,
        catalysts=catalysts,
        risks=risks,
    )
    return DecisionPacket(
        symbol=str(getattr(signal, "code", "") or "").strip().upper(),
        instrument_type=str(getattr(signal, "instrument_type", "STOCK") or "STOCK").strip().upper(),
        effective_trade_date=getattr(signal, "effective_trade_date", None),
        direction=str(getattr(signal, "direction", "neutral") or "neutral").strip().lower(),
        forecast_score=_finite(getattr(signal, "forecast_score", None)),
        horizon_forecasts=getattr(signal, "horizon_forecasts", {}) or {},
        quality_score=_finite(getattr(signal, "quality_score", None)),
        opportunity_score=_finite(getattr(signal, "opportunity_score", None)),
        risk_score=_finite(getattr(signal, "risk_score", None)),
        evidence_coverage=float(getattr(signal, "evidence_coverage", 0.0) or 0.0),
        assessment=assessment,
        execution=execution,
        catalysts=catalysts,
        risks=risks,
        limitations=_text_tuple(getattr(signal, "limitations", ())),
        engine_version=str(diagnostics.get("engine_version") or "unknown"),
        feature_adapter_version=(
            str(diagnostics.get("feature_adapter_version"))
            if diagnostics.get("feature_adapter_version") is not None
            else None
        ),
    )
