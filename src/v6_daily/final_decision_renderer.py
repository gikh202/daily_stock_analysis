from __future__ import annotations

import re
from typing import Iterable

from .fusion_contracts import (
    FinalDecisionPacket,
    action_label_zh,
    agreement_label_zh,
    verdict_label_zh,
)


def _section_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^(?P<header>### \d+\. {re.escape(symbol)}(?: · .*?)? · 最终：[^\n]+)\n"
        rf"(?P<body>.*?)(?=^### \d+\. |^## 4\. |\Z)"
    )


def assert_final_decision_report_consistency(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> None:
    """Fail closed if presentation drifts from the typed final contract.

    This module deliberately performs validation only. It must never repair or
    re-derive a business decision from Markdown because FinalDecisionPacket is
    already the single source used by unified_report to render the decision.
    """
    text = str(report or "")
    errors: list[str] = []
    for packet in packets:
        if not packet.symbol:
            continue
        match = _section_pattern(packet.symbol).search(text)
        if not match:
            errors.append(f"{packet.symbol}: section missing")
            continue
        section = match.group(0)
        expected_verdict = f"**是否值得买**：**{verdict_label_zh(packet)}**"
        expected_action = f"最终：{action_label_zh(packet)}"
        expected_agreement = agreement_label_zh(packet)
        if expected_verdict not in section:
            errors.append(f"{packet.symbol}: final verdict drift")
        if expected_action not in section:
            errors.append(f"{packet.symbol}: final action drift")
        if expected_agreement not in section:
            errors.append(f"{packet.symbol}: fusion agreement drift")
    if errors:
        raise ValueError("FinalDecisionPacket/report mismatch: " + "; ".join(errors))


def apply_final_decision_contract(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> str:
    """Compatibility shim: validate typed rendering and return it unchanged.

    Before the single-source cutover this function rewrote stale legacy
    decisions in Markdown. Rewriting is intentionally removed: a mismatch now
    fails closed instead of silently mutating presentation text.
    """
    text = str(report or "")
    assert_final_decision_report_consistency(text, packets)
    return text
