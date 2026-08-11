from __future__ import annotations

import re
from typing import Iterable

from .fusion_contracts import (
    FinalDecisionPacket,
    action_label_zh,
    agreement_label_zh,
    render_final_decision_lines,
    verdict_label_zh,
)


_DECISION_BLOCK_RE = re.compile(
    r"(?m)^- \*\*是否值得买\*\*：.*\n"
    r"^  - \*\*支持买入的证据\*\*：.*\n"
    r"^  - \*\*支持等待/不买的证据\*\*：.*\n"
    r"^  - \*\*关键分界\*\*：.*$"
)


def _section_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^(?P<header>### \d+\. {re.escape(symbol)}(?: · .*?)? · 最终：[^\n]+)\n"
        rf"(?P<body>.*?)(?=^### \d+\. |^## 4\. |\Z)"
    )


def _rewrite_section(section_header: str, section_body: str, packet: FinalDecisionPacket) -> tuple[str, str]:
    action = action_label_zh(packet)
    agreement = agreement_label_zh(packet)
    header = re.sub(
        r" · 最终：[^·\n]+ · [^\n]+$",
        f" · 最终：{action} · {agreement}",
        section_header,
    )
    block = "\n".join(render_final_decision_lines(packet))
    if _DECISION_BLOCK_RE.search(section_body):
        body = _DECISION_BLOCK_RE.sub(block, section_body, count=1)
    else:
        conclusion = re.search(r"(?m)^- \*\*最终结论\*\*：.*$", section_body)
        if conclusion:
            insert_at = conclusion.end()
            body = section_body[:insert_at] + "\n" + block + section_body[insert_at:]
        else:
            body = block + "\n" + section_body
    return header, body


def _rewrite_priority_table(report: str, packets: Iterable[FinalDecisionPacket]) -> str:
    by_symbol = {packet.symbol: packet for packet in packets if packet.symbol}
    result: list[str] = []
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "---" not in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 10:
                symbol = cells[1].upper()
                packet = by_symbol.get(symbol)
                if packet is not None:
                    cells[2] = action_label_zh(packet)
                    cells[3] = agreement_label_zh(packet)
                    line = "| " + " | ".join(cells) + " |"
        result.append(line)
    return "\n".join(result)


def apply_final_decision_contract(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> str:
    """Make FinalDecisionPacket the outward-facing decision source.

    The legacy integrated renderer can remain in shadow mode during migration,
    but its final action/decision block is overwritten from the typed contract
    before the Markdown is written or sent to notification channels.
    """
    text = str(report or "")
    packet_list = tuple(packets)
    for packet in packet_list:
        if not packet.symbol:
            continue
        pattern = _section_pattern(packet.symbol)
        match = pattern.search(text)
        if not match:
            continue
        header, body = _rewrite_section(match.group("header"), match.group("body"), packet)
        text = text[: match.start()] + header + "\n" + body + text[match.end() :]
    text = _rewrite_priority_table(text, packet_list)
    return text


def assert_final_decision_report_consistency(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> None:
    """Fail closed if rendered output drifts from the typed final contract."""
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
