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
EXECUTION_CONTRACT_VERSION = "v7.2"
FULL_APPROVED = "FULL_APPROVED"
CONDITIONAL_APPROVED = "CONDITIONAL_APPROVED"
REJECTED = "REJECTED"


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


def _wait_is_conditionally_buyable(packet: FinalDecisionPacket) -> bool:
    """Separate a buyable WAIT from a true close-layer rejection.

    V6 WAIT means the deterministic layer did not produce an executable setup;
    it does not automatically mean the investment thesis is invalid. V7.2 only
    promotes WAIT to conditional approval when the cross-layer direction remains
    constructive and the existing hard risk blockers are absent.
    """
    return bool(
        packet.v6_decision == "WAIT"
        and packet.fusion_complete
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
        raw = build_final_decision_packet(item, v4_views.get(code))
        packets.append(_upgrade_execution_contract(raw))
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
