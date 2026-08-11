from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .fusion_contracts import FinalDecisionPacket, build_final_decision_packet
from .v4_research_adapter import latest_v4_views


FINAL_DECISION_PAYLOAD_VERSION = "final-decision-payload-v1"


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
        packets.append(build_final_decision_packet(item, v4_views.get(code)))
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
    return {
        "version": FINAL_DECISION_PAYLOAD_VERSION,
        "source_payload_version": payload.get("version"),
        "summary": {
            "symbols": len(packets),
            "fusion_complete": complete,
            "final_assessments": final,
            "worth_buying": buyable,
            "not_worth_buying_now": blocked,
            "unresolved": unresolved,
            "execution_authorized": actionable,
        },
        "packets": [packet.to_dict() for packet in packets],
    }
