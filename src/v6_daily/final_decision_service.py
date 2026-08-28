from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Sequence

from .fusion_contracts import (
    FinalDecisionPacket,
    FinalVerdict,
    FusionAgreement,
    build_final_decision_packet,
)
from .v4_research_adapter import latest_v4_views


FINAL_DECISION_PAYLOAD_VERSION = "final-decision-payload-v1"
EXECUTION_CONTRACT_VERSION = "v7.4"
FULL_APPROVED = "FULL_APPROVED"
CONDITIONAL_APPROVED = "CONDITIONAL_APPROVED"
REJECTED = "REJECTED"
V73_RELIABILITY_MIN_MATURE_SAMPLES = 50
V73_RESEARCH_ONLY_MARKER = "研究观察"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _horizon_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    if text.endswith("days") and text[:-4].isdigit():
        return f"{int(text[:-4])}d"
    if text.endswith("day") and text[:-3].isdigit():
        return f"{int(text[:-3])}d"
    if text.endswith("d") and text[:-1].isdigit():
        return f"{int(text[:-1])}d"
    return text


def _v7_horizon_block(v6: Mapping[str, Any], horizon: str) -> Mapping[str, Any]:
    key = _horizon_key(horizon)
    direct = _mapping(v6.get("horizon_forecasts"))
    block = _mapping(direct.get(key))
    if block:
        return block
    intelligence = _mapping(
        _mapping(v6.get("context_features")).get("forecast_intelligence")
    )
    return _mapping(_mapping(intelligence.get("horizons")).get(key))


def _research_horizon_is_quarantined(
    v6: Mapping[str, Any],
    horizon: str,
) -> bool:
    """Return True when an upstream research horizon must not affect execution fusion."""
    key = _horizon_key(horizon)
    if key != "10d":
        return False
    block = _v7_horizon_block(v6, key)
    if not block:
        return False
    diagnostics = _mapping(block.get("diagnostics"))
    weight = _finite(diagnostics.get("decision_weight"))
    if weight is None:
        weight = _finite(block.get("decision_weight"))
    try:
        samples = int(block.get("calibration_samples") or 0)
    except (TypeError, ValueError):
        samples = 0
    status = str(block.get("calibration_status") or "prior_only").strip().lower()
    if weight is not None:
        return weight <= 0.0
    return status != "mature" or samples < V73_RELIABILITY_MIN_MATURE_SAMPLES


def _reliability_filtered_v4(
    v6: Mapping[str, Any],
    v4: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    if not isinstance(v4, Mapping):
        return v4
    forecast = dict(_mapping(v4.get("forecast")))
    horizon = _horizon_key(forecast.get("horizon") or "10d") or "10d"
    if not _research_horizon_is_quarantined(v6, horizon):
        return v4

    original_direction = forecast.get("direction")
    original_return = forecast.get("expected_return_pct")
    forecast["research_direction"] = original_direction
    forecast["research_expected_return_pct"] = original_return
    forecast["direction"] = "neutral"
    forecast["expected_return_pct"] = None
    forecast["horizon"] = f"{horizon}（{V73_RESEARCH_ONLY_MARKER}）"
    forecast["reliability_quarantined"] = True
    forecast["reliability_reason"] = (
        "V7.4 reliability gate requires >=50 mature forward samples and usable "
        "directional accuracy before a research horizon may influence execution"
    )
    filtered = dict(v4)
    filtered["forecast"] = forecast
    return filtered


def _execution_view(v6: Mapping[str, Any]) -> Dict[str, Any]:
    """Use reliability-aware direction for execution without rewriting research history."""
    view = dict(v6)
    diagnostics = _mapping(v6.get("diagnostics"))
    trading_direction = str(diagnostics.get("trading_direction") or "").strip().lower()
    if trading_direction in {"bullish", "neutral", "bearish"}:
        view["direction"] = trading_direction
    return view


def _uses_quarantined_research(packet: FinalDecisionPacket) -> bool:
    return V73_RESEARCH_ONLY_MARKER in str(packet.v4_horizon or "")


def _constructive_direction(packet: FinalDecisionPacket) -> bool:
    directions = {packet.v4_direction, packet.v6_direction}
    return "bullish" in directions and "bearish" not in directions


def _risk_heavy(packet: FinalDecisionPacket) -> bool:
    risk = packet.risk_score
    opportunity = packet.opportunity_score
    return bool(
        (risk is not None and risk >= 60.0)
        or (risk is not None and opportunity is not None and risk >= opportunity)
    )


def _quarantined_wait_can_remain_conditional(packet: FinalDecisionPacket) -> bool:
    """Low-sample uncertainty is not itself a hard risk rejection."""
    return bool(
        packet.v6_decision == "WAIT"
        and not _risk_heavy(packet)
        and packet.v4_operation not in {"卖出", "减仓"}
    )


def _apply_reliability_guard(packet: FinalDecisionPacket) -> FinalDecisionPacket:
    """Quarantine unreliable research without converting uncertainty into rejection."""
    if not _uses_quarantined_research(packet):
        return packet

    if _quarantined_wait_can_remain_conditional(packet):
        assessment = replace(
            packet.assessment,
            verdict=FinalVerdict.CONDITIONAL_BUY,
            worth_buying=True,
            execution_authorized=False,
            rationale=(
                "V7.4 reliability gate quarantined the upstream research horizon, "
                "but low sample reliability is uncertainty rather than a hard risk veto. "
                "The WAIT thesis remains conditionally observable intraday; execution "
                "still requires a complete risk-bounded plan or declared confirmation."
            ),
        )
        return replace(packet, assessment=assessment)

    verdict = (
        FinalVerdict.WAIT
        if packet.v6_decision in {"WAIT", "AVOID"}
        else FinalVerdict.WATCH
    )
    assessment = replace(
        packet.assessment,
        verdict=verdict,
        worth_buying=False if packet.v6_decision in {"WAIT", "AVOID"} else None,
        execution_authorized=False,
        rationale=(
            "V7.4 reliability gate quarantined the upstream research horizon. "
            "A separate hard risk/avoid condition remains authoritative, so the "
            "quarantined research cannot create conditional approval or execution authorization."
        ),
    )
    return replace(packet, assessment=assessment)


def _wait_is_conditionally_buyable(packet: FinalDecisionPacket) -> bool:
    """Separate a buyable WAIT from a true close-layer rejection."""
    return bool(
        packet.v6_decision == "WAIT"
        and packet.fusion_complete
        and not _uses_quarantined_research(packet)
        and packet.agreement is not FusionAgreement.CONFLICT
        and packet.v4_operation not in {"卖出", "减仓"}
        and _constructive_direction(packet)
        and not _risk_heavy(packet)
    )


def _upgrade_execution_contract(packet: FinalDecisionPacket) -> FinalDecisionPacket:
    """Upgrade the close-layer semantic contract without inventing a trade plan."""
    if not _wait_is_conditionally_buyable(packet):
        return packet

    assessment = replace(
        packet.assessment,
        verdict=FinalVerdict.CONDITIONAL_BUY,
        worth_buying=True,
        execution_authorized=False,
        rationale=(
            "V6 chose WAIT because no executable deterministic setup is active, "
            "but cross-layer direction remains constructive and no hard risk blocker "
            "is present. The thesis is conditionally approved for monitoring only; "
            "execution still requires a complete risk-bounded plan or declared entry condition."
        ),
    )
    return replace(packet, assessment=assessment)


def _execution_status(packet: FinalDecisionPacket) -> str:
    if packet.assessment.execution_authorized:
        return FULL_APPROVED
    if packet.assessment.worth_buying is True:
        return CONDITIONAL_APPROVED
    return REJECTED


def _serialize_packet(packet: FinalDecisionPacket) -> Dict[str, Any]:
    data = packet.to_dict()
    assessment = data.setdefault("assessment", {})
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    status = _execution_status(packet)
    assessment["execution_status"] = status

    if status == CONDITIONAL_APPROVED:
        entry_zone = execution.get("entry_zone")
        conditional_price = None
        if isinstance(entry_zone, (list, tuple)) and len(entry_zone) == 2:
            try:
                candidate = float(entry_zone[1])
            except (TypeError, ValueError):
                candidate = 0.0
            if candidate > 0:
                conditional_price = candidate
        assessment["conditional_entry_price"] = conditional_price
        assessment["conditional_entry_reason"] = (
            "wait_for_declared_entry_zone_or_confirmation"
            if conditional_price is not None
            else "wait_for_complete_executable_plan"
        )
    else:
        assessment["conditional_entry_price"] = None
        assessment["conditional_entry_reason"] = None

    data["execution_contract"] = {
        "version": EXECUTION_CONTRACT_VERSION,
        "status": status,
        "authorized": status == FULL_APPROVED,
    }
    return data


def build_final_decision_packets(
    payload: Mapping[str, Any],
    *,
    v4_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> tuple[FinalDecisionPacket, ...]:
    board = [dict(item) for item in (payload.get("board") or []) if isinstance(item, dict)]
    v4_views = latest_v4_views(v4_records)
    packets: list[FinalDecisionPacket] = []
    for item in board:
        code = str(item.get("code") or "").strip().upper()
        execution_item = _execution_view(item)
        filtered_v4 = _reliability_filtered_v4(execution_item, v4_views.get(code))
        raw = build_final_decision_packet(execution_item, filtered_v4)
        guarded = _apply_reliability_guard(raw)
        packets.append(_upgrade_execution_contract(guarded))
    return tuple(packets)


def build_final_decision_payload(
    payload: Mapping[str, Any],
    *,
    v4_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    packets = build_final_decision_packets(payload, v4_records=v4_records)
    complete = sum(1 for packet in packets if packet.fusion_complete)
    final = sum(1 for packet in packets if packet.assessment.is_final)
    actionable = sum(1 for packet in packets if packet.assessment.execution_authorized)
    buyable = sum(1 for packet in packets if packet.assessment.worth_buying is True)
    blocked = sum(1 for packet in packets if packet.assessment.worth_buying is False)
    unresolved = len(packets) - buyable - blocked
    execution_counts = {
        FULL_APPROVED: sum(_execution_status(packet) == FULL_APPROVED for packet in packets),
        CONDITIONAL_APPROVED: sum(
            _execution_status(packet) == CONDITIONAL_APPROVED for packet in packets
        ),
        REJECTED: sum(_execution_status(packet) == REJECTED for packet in packets),
    }
    return {
        "version": FINAL_DECISION_PAYLOAD_VERSION,
        "source_payload_version": payload.get("version"),
        "execution_contract_version": EXECUTION_CONTRACT_VERSION,
        "summary": {
            "symbols": len(packets),
            "fusion_complete": complete,
            "final_assessments": final,
            "worth_buying": buyable,
            "not_worth_buying_now": blocked,
            "unresolved": unresolved,
            "execution_authorized": actionable,
            "execution_status_counts": execution_counts,
        },
        "packets": [_serialize_packet(packet) for packet in packets],
    }
