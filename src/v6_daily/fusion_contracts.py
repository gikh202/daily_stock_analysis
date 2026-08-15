from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .decision_contracts import ExecutionPlanContract, build_execution_plan


FINAL_DECISION_PACKET_SCHEMA_VERSION = "final-decision-packet-v1"
FINAL_ASSESSMENT_SCOPE = "v4_v6_final_fusion"


class FusionAgreement(str, Enum):
    ALIGNED = "aligned"
    PARTIAL = "partial"
    NEUTRAL = "neutral"
    CONFLICT = "conflict"
    V4_MISSING = "v4_missing"


class FinalVerdict(str, Enum):
    BUY_BY_PLAN = "buy_by_plan"
    CONDITIONAL_BUY = "conditional_buy"
    WATCH = "watch"
    WAIT = "wait"
    AVOID = "avoid"
    DATA_INCOMPLETE = "data_incomplete"


@dataclass(frozen=True)
class FinalDecisionAssessment:
    verdict: FinalVerdict
    worth_buying: Optional[bool]
    execution_authorized: bool
    rationale: str
    bullish_evidence: Tuple[str, ...] = ()
    bearish_evidence: Tuple[str, ...] = ()
    key_boundaries: Tuple[str, ...] = ()
    scope: str = FINAL_ASSESSMENT_SCOPE
    is_final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "is_final": self.is_final,
            "verdict": self.verdict.value,
            "worth_buying": self.worth_buying,
            "execution_authorized": self.execution_authorized,
            "rationale": self.rationale,
            "bullish_evidence": list(self.bullish_evidence),
            "bearish_evidence": list(self.bearish_evidence),
            "key_boundaries": list(self.key_boundaries),
        }


@dataclass(frozen=True)
class FinalDecisionPacket:
    symbol: str
    instrument_type: str
    effective_trade_date: Optional[str]
    v4_direction: str
    v6_direction: str
    v4_horizon: str
    v4_expected_return_pct: Optional[float]
    v6_forecast_score: Optional[float]
    opportunity_score: Optional[float]
    risk_score: Optional[float]
    evidence_coverage: float
    agreement: FusionAgreement
    assessment: FinalDecisionAssessment
    execution: ExecutionPlanContract
    v4_operation: str
    v6_decision: str
    non_trading: bool
    schema_version: str = FINAL_DECISION_PACKET_SCHEMA_VERSION

    @property
    def fusion_complete(self) -> bool:
        return self.agreement is not FusionAgreement.V4_MISSING

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": {
                "symbol": self.symbol,
                "instrument_type": self.instrument_type,
                "effective_trade_date": self.effective_trade_date,
            },
            "fusion": {
                "complete": self.fusion_complete,
                "agreement": self.agreement.value,
                "v4_direction": self.v4_direction,
                "v6_direction": self.v6_direction,
                "v4_horizon": self.v4_horizon,
                "v4_expected_return_pct": self.v4_expected_return_pct,
                "v6_forecast_score": self.v6_forecast_score,
                "v4_operation": self.v4_operation,
                "v6_decision": self.v6_decision,
                "non_trading": self.non_trading,
            },
            "scores": {
                "opportunity": self.opportunity_score,
                "risk": self.risk_score,
                "evidence_coverage": self.evidence_coverage,
            },
            "assessment": self.assessment.to_dict(),
            "execution": self.execution.to_dict(),
        }


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _normalize_direction(value: Any) -> str:
    text = _text(value).lower()
    if text in {"bullish", "看多", "强烈看多", "偏多", "上涨"}:
        return "bullish"
    if text in {"bearish", "看空", "强烈看空", "偏空", "下跌"}:
        return "bearish"
    if text in {"neutral", "中性", "震荡", "横盘"}:
        return "neutral"
    if "看多" in text or "bull" in text:
        return "bullish"
    if "看空" in text or "bear" in text:
        return "bearish"
    return "neutral"


def _dedupe(items: Iterable[Any], *, limit: int) -> Tuple[str, ...]:
    result: list[str] = []
    normalized: set[str] = set()
    for raw in items:
        text = _text(raw)
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).strip().lower()
        key = re.sub(r"\[e\d+\]", "", key).strip()
        if not key or key in normalized:
            continue
        normalized.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, (list, tuple)):
        return value
    return ()


def _is_non_trading(v4: Mapping[str, Any]) -> bool:
    if v4.get("is_trading_day") is False:
        return True
    return _text(v4.get("phase")).lower() in {"non_trading", "closed"}


def _agreement(v6_direction: str, v4_direction: str) -> FusionAgreement:
    if v6_direction == v4_direction and v6_direction != "neutral":
        return FusionAgreement.ALIGNED
    if v6_direction == v4_direction:
        return FusionAgreement.NEUTRAL
    if "neutral" in {v6_direction, v4_direction}:
        return FusionAgreement.PARTIAL
    return FusionAgreement.CONFLICT


def _bullish_and_bearish_evidence(
    v6: Mapping[str, Any],
    v4: Mapping[str, Any],
    *,
    v4_direction: str,
) -> tuple[Tuple[str, ...], Tuple[str, ...]]:
    bullish_candidates: list[Any] = [
        v4.get("strongest_bullish"),
        *_sequence(v4.get("catalysts")),
        *_sequence(v6.get("catalysts")),
    ]
    bearish_candidates: list[Any] = [
        v4.get("strongest_bearish"),
        *_sequence(v4.get("risks")),
        *_sequence(v6.get("risks")),
        v4.get("risk_warning"),
    ]
    forecast = _mapping(v4.get("forecast"))
    forecast_rationale = _text(forecast.get("rationale"))
    if forecast_rationale:
        if v4_direction == "bullish":
            bullish_candidates.append(forecast_rationale)
        elif v4_direction == "bearish":
            bearish_candidates.append(forecast_rationale)
    return _dedupe(bullish_candidates, limit=4), _dedupe(bearish_candidates, limit=4)


def _key_boundaries(
    v6: Mapping[str, Any],
    v4: Mapping[str, Any],
    execution: ExecutionPlanContract,
) -> Tuple[str, ...]:
    boundaries: list[Any] = []
    if execution.has_active_plan:
        if execution.entry_zone:
            boundaries.append(f"确定性入场区间 {list(execution.entry_zone)}")
        boundaries.append(f"最大仓位上限 {100.0 * execution.max_position_pct:.1f}%")
        if execution.stop_loss is not None:
            boundaries.append(f"失效/止损 {execution.stop_loss}")
    boundaries.extend(_sequence(v4.get("watch_conditions")))
    if not boundaries and v4.get("immediate_action"):
        boundaries.append(v4.get("immediate_action"))
    return _dedupe(boundaries, limit=6)


def build_final_decision_packet(
    v6: Mapping[str, Any],
    v4: Optional[Mapping[str, Any]],
) -> FinalDecisionPacket:
    """Fuse normalized V4 research with deterministic V6 into one final contract.

    This is the only layer allowed to answer the cross-layer question "is it
    worth buying?". The V6-only DecisionPacket remains pre-fusion and cannot
    promote WATCH to a final conditional buy by itself.
    """
    v4_map = _mapping(v4)
    forecast = _mapping(v4_map.get("forecast"))
    v6_decision = _text(v6.get("decision")).upper() or "WAIT"
    v6_direction = _normalize_direction(v6.get("direction"))
    v4_direction = _normalize_direction(
        forecast.get("direction") or v4_map.get("trend_prediction")
    )
    execution = build_execution_plan(v6_decision, _mapping(v6.get("trade_plan")))
    opportunity = _finite(v6.get("opportunity_score"))
    risk = _finite(v6.get("risk_score"))
    non_trading = _is_non_trading(v4_map)
    v4_operation = _text(v4_map.get("operation"))

    if not v4_map:
        agreement = FusionAgreement.V4_MISSING
        assessment = FinalDecisionAssessment(
            verdict=FinalVerdict.DATA_INCOMPLETE,
            worth_buying=None,
            execution_authorized=False,
            rationale="V4 structured research is missing; V6 alone cannot produce the final cross-layer buy verdict",
            bullish_evidence=_dedupe(_sequence(v6.get("catalysts")), limit=4),
            bearish_evidence=_dedupe(_sequence(v6.get("risks")), limit=4),
            key_boundaries=_key_boundaries(v6, {}, execution),
            is_final=False,
        )
        return FinalDecisionPacket(
            symbol=_text(v6.get("code")).upper(),
            instrument_type=_text(v6.get("instrument_type") or "STOCK").upper(),
            effective_trade_date=_text(v6.get("effective_trade_date")) or None,
            v4_direction="neutral",
            v6_direction=v6_direction,
            v4_horizon="",
            v4_expected_return_pct=None,
            v6_forecast_score=_finite(v6.get("forecast_score")),
            opportunity_score=opportunity,
            risk_score=risk,
            evidence_coverage=float(_finite(v6.get("evidence_coverage")) or 0.0),
            agreement=agreement,
            assessment=assessment,
            execution=execution,
            v4_operation="",
            v6_decision=v6_decision,
            non_trading=False,
        )

    agreement = _agreement(v6_direction, v4_direction)
    bullish, bearish = _bullish_and_bearish_evidence(
        v6, v4_map, v4_direction=v4_direction
    )
    boundaries = _key_boundaries(v6, v4_map, execution)
    direction_conflict = agreement is FusionAgreement.CONFLICT
    constructive_direction = (
        "bullish" in {v4_direction, v6_direction}
        and "bearish" not in {v4_direction, v6_direction}
    )
    risk_heavy = bool(
        (risk is not None and risk >= 60.0)
        or (opportunity is not None and risk is not None and risk >= opportunity)
    )
    v4_blocks_buy = v4_operation in {"卖出", "减仓"}

    verdict: FinalVerdict
    worth_buying: Optional[bool]
    execution_authorized = False
    rationale: str

    if v6_decision == "AVOID":
        verdict = FinalVerdict.AVOID
        worth_buying = False
        rationale = (
            "V6 risk gate dominates the current setup; bullish evidence remains visible but does not authorize a new position"
        )
    elif v6_decision == "WAIT":
        verdict = FinalVerdict.WAIT
        worth_buying = False
        rationale = (
            "bullish research may remain valid, but the deterministic layer has not produced an executable setup"
        )
    elif v6_decision == "BUY_SETUP":
        if not execution.has_active_plan:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            rationale = "BUY_SETUP lacks a complete structured trade plan, so the final layer refuses to authorize execution"
        elif direction_conflict:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            rationale = "V4 and V6 directions directly conflict; the final layer waits for the conflict to converge"
        elif v4_blocks_buy:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            rationale = "V4 execution guidance is reducing or selling, so the final layer does not promote the setup to a buy"
        elif non_trading or v4_operation == "观望":
            verdict = FinalVerdict.WATCH
            worth_buying = True
            rationale = "the thesis is buyable, but V4 execution context still requires confirmation before a new position"
        else:
            verdict = FinalVerdict.BUY_BY_PLAN
            worth_buying = True
            execution_authorized = True
            rationale = "V4/V6 do not conflict and the deterministic active plan authorizes buying only within its risk boundaries"
    elif v6_decision == "WATCH":
        if direction_conflict:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            rationale = "V4 and V6 directions directly conflict, so neither side is allowed to dominate the final decision"
        elif risk_heavy:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            rationale = "risk is high or no lower than opportunity, so a potential setup is not promoted to conditional buy"
        elif v4_blocks_buy:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            rationale = "V4 execution guidance is reducing or selling, which blocks promotion to conditional buy"
        elif execution.has_active_plan and constructive_direction:
            verdict = FinalVerdict.CONDITIONAL_BUY
            worth_buying = True
            rationale = (
                "bullish evidence and deterministic direction are constructive with an active risk plan; entry still requires the listed confirmations, so execution remains unauthorized until confirmation"
            )
        else:
            verdict = FinalVerdict.WATCH
            worth_buying = None
            if not execution.has_active_plan:
                rationale = "potential opportunity exists but no complete deterministic trade plan is active"
            else:
                rationale = "evidence has not formed enough cross-layer directional convergence to promote the setup"
    else:
        verdict = FinalVerdict.WAIT
        worth_buying = None
        rationale = "unrecognized deterministic decision state; final layer defaults to no new execution"

    assessment = FinalDecisionAssessment(
        verdict=verdict,
        worth_buying=worth_buying,
        execution_authorized=execution_authorized,
        rationale=rationale,
        bullish_evidence=bullish,
        bearish_evidence=bearish,
        key_boundaries=boundaries,
    )
    return FinalDecisionPacket(
        symbol=_text(v6.get("code")).upper(),
        instrument_type=_text(v6.get("instrument_type") or "STOCK").upper(),
        effective_trade_date=_text(v6.get("effective_trade_date")) or None,
        v4_direction=v4_direction,
        v6_direction=v6_direction,
        v4_horizon=_text(forecast.get("horizon")) or "10d",
        v4_expected_return_pct=_finite(forecast.get("expected_return_pct")),
        v6_forecast_score=_finite(v6.get("forecast_score")),
        opportunity_score=opportunity,
        risk_score=risk,
        evidence_coverage=float(_finite(v6.get("evidence_coverage")) or 0.0),
        agreement=agreement,
        assessment=assessment,
        execution=execution,
        v4_operation=v4_operation,
        v6_decision=v6_decision,
        non_trading=non_trading,
    )


def verdict_label_zh(packet: FinalDecisionPacket) -> str:
    verdict = packet.assessment.verdict
    if verdict is FinalVerdict.BUY_BY_PLAN:
        return "可以买，但只按计划买"
    if verdict is FinalVerdict.CONDITIONAL_BUY:
        return "条件式可买"
    if verdict is FinalVerdict.AVOID:
        return "当前不买/回避"
    if verdict is FinalVerdict.WAIT:
        return "暂不买，等待确认"
    if verdict is FinalVerdict.DATA_INCOMPLETE:
        return "数据不足，不能形成最终买入判断"
    return "继续观察"


def action_label_zh(packet: FinalDecisionPacket) -> str:
    verdict = packet.assessment.verdict
    if verdict is FinalVerdict.BUY_BY_PLAN:
        return "买入准备"
    if verdict is FinalVerdict.CONDITIONAL_BUY:
        return "等待触发后买入"
    if verdict is FinalVerdict.AVOID:
        return "回避"
    if verdict is FinalVerdict.WAIT:
        return "等待"
    if verdict is FinalVerdict.DATA_INCOMPLETE:
        return "等待（V4缺失）"
    if packet.non_trading and packet.assessment.worth_buying:
        return "观察（等待开盘确认）"
    return "观察"


def agreement_label_zh(packet: FinalDecisionPacket) -> str:
    return {
        FusionAgreement.ALIGNED: "方向一致",
        FusionAgreement.PARTIAL: "部分一致",
        FusionAgreement.NEUTRAL: "共同偏中性",
        FusionAgreement.CONFLICT: "方向分歧",
        FusionAgreement.V4_MISSING: "V4结构化数据缺失",
    }[packet.agreement]


def direction_label_zh(direction: str) -> str:
    return {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(
        _normalize_direction(direction), "中性"
    )


def render_final_decision_lines(packet: FinalDecisionPacket) -> list[str]:
    assessment = packet.assessment
    basis = [f"投研{packet.v4_horizon or '10d'}预测{direction_label_zh(packet.v4_direction)}"]
    if packet.v4_expected_return_pct is not None:
        basis.append(f"预期收益{packet.v4_expected_return_pct:+.1f}%")
    basis.append(f"量化方向{direction_label_zh(packet.v6_direction)}")
    opportunity = "N/A" if packet.opportunity_score is None else f"{packet.opportunity_score:.1f}"
    risk = "N/A" if packet.risk_score is None else f"{packet.risk_score:.1f}"
    basis.append(f"机会/风险{opportunity}/{risk}")
    basis.append(agreement_label_zh(packet))

    rationale_zh = {
        FinalVerdict.BUY_BY_PLAN: "方向与执行层已达到买入准备，但仍必须服从入场、止损和最大仓位，不把看多等同于无条件追价。",
        FinalVerdict.CONDITIONAL_BUY: "当前仍需确认，不代表否定买入；只有进入确定性入场区间并满足确认条件时才执行。",
        FinalVerdict.AVOID: (
            "即使存在局部看多证据，风险闸门当前占优；除非风险条件明显改善，否则不建立新仓。"
            if assessment.bullish_evidence
            else "当前没有足够的结构化看多证据可以对冲风险闸门，维持回避，不建立新仓。"
        ),
        FinalVerdict.WAIT: (
            "看多逻辑仍保留，但量化与投研尚未形成足够共振或交易计划尚未触发，当前不执行。"
            if assessment.bullish_evidence
            else "当前缺少足够的结构化看多证据，且执行层未触发，维持等待而不是勉强构造买入理由。"
        ),
        FinalVerdict.DATA_INCOMPLETE: "V4结构化投研缺失，不能用V6单层观点冒充最终跨层买入结论。",
    }.get(FinalVerdict(assessment.verdict), "")
    if assessment.verdict is FinalVerdict.WATCH:
        if packet.agreement is FusionAgreement.CONFLICT:
            rationale_zh = "多空方向存在直接分歧，不能因为某一侧更乐观就提前买入；等待冲突收敛后再评估。"
        elif packet.risk_score is not None and (
            packet.risk_score >= 60.0
            or (
                packet.opportunity_score is not None
                and packet.risk_score >= packet.opportunity_score
            )
        ):
            rationale_zh = "当前风险分偏高或已不低于机会分，即使保留潜在买点也不足以升级为条件式买入。"
        elif not packet.execution.has_active_plan:
            rationale_zh = "存在潜在机会，但没有活动确定性交易计划；保留看多逻辑，同时等待明确的入场与风控边界。"
        else:
            rationale_zh = "执行层尚未达到可操作条件；保留现有多空证据，等待价格、量价或事件确认。"

    lines = [
        f"- **是否值得买**：**{verdict_label_zh(packet)}**。{'，'.join(basis)}。{rationale_zh}"
    ]
    lines.append(
        "  - **支持买入的证据**："
        + (
            "；".join(assessment.bullish_evidence)
            if assessment.bullish_evidence
            else "暂无足够的结构化看多证据，不能为了凑结论补造理由。"
        )
    )
    lines.append(
        "  - **支持等待/不买的证据**："
        + (
            "；".join(assessment.bearish_evidence)
            if assessment.bearish_evidence
            else "暂无足够的结构化看空证据；当前保守动作主要来自执行层未触发或方向未共振。"
        )
    )
    lines.append(
        "  - **关键分界**："
        + (
            "；".join(assessment.key_boundaries)
            if assessment.key_boundaries
            else "暂无足够的结构化确认条件，继续观察而不主动下单。"
        )
    )
    return lines