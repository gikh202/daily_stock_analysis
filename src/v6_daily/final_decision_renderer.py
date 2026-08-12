from __future__ import annotations

import re
from typing import Iterable, Sequence

from .fusion_contracts import (
    FinalDecisionPacket,
    action_label_zh,
    agreement_label_zh,
    verdict_label_zh,
)


_REPORT_DATE_RE = re.compile(
    r"(?m)^(# (?:AI )?美股(?:综合|决策)日报 · )\d{4}-\d{2}-\d{2}\s*$"
)
_DATA_LIMITATION_LINE_RE = re.compile(r"(?m)^- \*\*数据限制\*\*[:：].*$")
_NEWS_LINE_RE = re.compile(r"(?m)^- \*\*舆情/新闻\*\*[:：].*$")
_NEWS_EVIDENCE_UNAVAILABLE = (
    "source-backed deterministic catalyst unavailable",
    "无近期新闻",
    "暂无已验证的近期证据",
    "新闻证据缺失",
)
_US_PRICE_NUMBER = (
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:\s*[-–—~～至]\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?"
)
_US_PRICE_YUAN_RE = re.compile(rf"(?<![\d.,])\$?({_US_PRICE_NUMBER})\s*元")
_NEXT_CHECK_LINE_RE = re.compile(r"^(?P<prefix>.*?下次检查(?:\*\*)?)[:：].*$")
_NEXT_CHECK_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")


def _section_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ms)^(?P<header>### \d+\. {re.escape(symbol)}(?: · .*?)? · 最终：[^\n]+)\n"
        rf"(?P<body>.*?)(?=^### \d+\. |^## 4\. |\Z)"
    )


def _execution_position_pct(packet: FinalDecisionPacket) -> float:
    if not packet.assessment.execution_authorized or not packet.execution.has_active_plan:
        return 0.0
    return 100.0 * float(packet.execution.max_position_pct)


def _execution_authorization_lines(packet: FinalDecisionPacket) -> list[str]:
    authorized = bool(packet.assessment.execution_authorized)
    lines = [
        f"- **当前执行授权**：**{'是' if authorized else '否'}**",
        f"- **当前可执行仓位上限**：**{_execution_position_pct(packet):.1f}%**",
    ]
    if packet.execution.has_active_plan and not authorized:
        lines.append(
            f"- **条件触发后最大仓位上限**：**{100.0 * float(packet.execution.max_position_pct):.1f}%**"
        )
    return lines


def _news_evidence_unavailable(section: str) -> bool:
    lowered = section.lower()
    return any(marker.lower() in lowered for marker in _NEWS_EVIDENCE_UNAVAILABLE)


def _normalize_news_presentation(section: str) -> str:
    """Fail safe when current news evidence is unavailable.

    Missing evidence cannot support claims such as "sentiment is neutral" or
    "there is no catalyst". Keep the raw upstream research in artifacts, while
    the final investor report states only the evidence limitation.
    """
    if not _news_evidence_unavailable(section):
        return section
    text = _NEWS_LINE_RE.sub(
        "- **舆情/新闻**：近期新闻证据不足，无法可靠判断当前舆情方向或是否存在新增催化/利空。",
        section,
    )
    return text.replace("暂无利好催化", "近期催化证据不足")


def _normalize_us_price_units(section: str) -> str:
    """Render bare CNY-style stock price suffixes as USD in U.S. stock cards.

    These sections are exclusively U.S. equities/ETFs. A bare numeric ``元``
    attached directly to a technical/watch price is therefore a presentation
    leak from the upstream Chinese template, not a valid currency conversion.
    Amounts such as ``900亿美元`` are unaffected because ``元`` is not directly
    attached to the numeric token.
    """
    return _US_PRICE_YUAN_RE.sub(r"$\1", section)


def _normalize_next_check_presentation(section: str) -> str:
    """Canonicalize the next live confirmation checkpoint to the 09:45 ET run.

    The production workflow's confirmation run is 15 minutes after the regular
    U.S. open. Upstream research text may say 09:30, "open +30", or emit an ISO
    timestamp. Preserve the upstream trading date, but present one deterministic
    checkpoint in the final report.

    Keep the original line endings. ``_normalize_report_presentation`` replaces
    one matched symbol section at a time; dropping the trailing newline here can
    glue the next ``### N. SYMBOL`` heading onto the previous line. The validator
    then treats the following symbol as missing and may accidentally validate its
    content as part of the previous symbol.
    """
    normalized: list[str] = []
    for raw_line in section.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        ending = raw_line[len(line) :]
        if "下次检查" not in line:
            normalized.append(raw_line)
            continue
        date_match = _NEXT_CHECK_DATE_RE.search(line)
        line_match = _NEXT_CHECK_LINE_RE.match(line)
        if not date_match or not line_match:
            normalized.append(raw_line)
            continue
        normalized.append(
            f"{line_match.group('prefix')}：**{date_match.group(0)} 09:45 ET（开盘后15分钟）**"
            f"{ending}"
        )
    return "".join(normalized)


def _data_availability_summary(section: str) -> str | None:
    lowered = section.lower()
    limitations: list[str] = []
    if _news_evidence_unavailable(section):
        limitations.append("近期新闻/催化证据不足")
    if "盘中" in section and any(
        token in section for token in ("不可用", "缺失", "覆盖不可用")
    ):
        limitations.append("盘中技术数据不可用")
    if any(
        token in lowered
        for token in (
            "quote: fallback",
            "行情实时数据降级",
            "行情数据源为yfinance降级备份",
        )
    ):
        limitations.append("实时行情存在降级/回退")
    if not limitations:
        return None
    return "- **数据可用性**：" + "；".join(dict.fromkeys(limitations)) + "。"


def _normalize_section_presentation(
    section: str,
    packet: FinalDecisionPacket,
) -> str:
    """Normalize presentation from the already-authoritative final packet.

    No business decision is derived here. The function only makes execution
    authorization explicit and prevents upstream V4 guidance from being
    presented as the final execution instruction.
    """
    text = _normalize_news_presentation(section)
    text = _normalize_us_price_units(text)
    text = _normalize_next_check_presentation(text)

    text = text.replace("**预测层 vs 执行层**", "**预测层 vs 上游投研层**")
    if packet.v4_operation:
        text = re.sub(
            r"当前执行\s+\*\*[^*\n]+\*\*",
            f"上游投研动作 **{packet.v4_operation}**（非最终执行）",
            text,
        )
    else:
        text = re.sub(
            r"当前执行\s+\*\*[^*\n]+\*\*",
            "上游投研动作 **未知**（非最终执行）",
            text,
        )

    authorized = bool(packet.assessment.execution_authorized)
    if packet.execution.has_active_plan and not authorized:
        text = text.replace(
            "**V6 最大仓位上限**",
            "**V6 条件触发后最大仓位上限**",
        )
        text = text.replace(
            "（优先采用 V6 确定性风控计划）",
            "（条件参考，当前未获执行授权）",
        )
        cap = 100.0 * float(packet.execution.max_position_pct)
        text = text.replace(
            f"最大仓位上限 {cap:.1f}%",
            f"条件触发后最大仓位上限 {cap:.1f}%",
        )

    auth_lines = _execution_authorization_lines(packet)
    if "**当前执行授权**" not in text:
        lines = text.splitlines()
        insertion = next(
            (
                index + 1
                for index, line in enumerate(lines)
                if line.startswith("- **是否值得买**")
            ),
            1,
        )
        lines[insertion:insertion] = auth_lines
        text = "\n".join(lines)

    availability = _data_availability_summary(text)
    if availability and "**数据可用性**" not in text:
        match = _DATA_LIMITATION_LINE_RE.search(text)
        if match:
            text = text[: match.start()] + availability + "\n" + text[match.start() :]
        else:
            text = text.rstrip() + "\n" + availability + "\n"

    return text


def _effective_report_date(packets: Sequence[FinalDecisionPacket]) -> str | None:
    dates = sorted(
        {
            str(packet.effective_trade_date or "").strip()
            for packet in packets
            if re.fullmatch(
                r"\d{4}-\d{2}-\d{2}",
                str(packet.effective_trade_date or "").strip(),
            )
        }
    )
    return dates[-1] if dates else None


def _normalize_report_presentation(
    report: str,
    packets: Sequence[FinalDecisionPacket],
) -> str:
    text = str(report or "")
    effective_date = _effective_report_date(packets)
    if effective_date:
        text = _REPORT_DATE_RE.sub(rf"\g<1>{effective_date}", text, count=1)

    for packet in packets:
        if not packet.symbol:
            continue
        pattern = _section_pattern(packet.symbol)
        match = pattern.search(text)
        if not match:
            continue
        normalized = _normalize_section_presentation(match.group(0), packet)
        text = text[: match.start()] + normalized + text[match.end() :]
    return text


def assert_final_decision_report_consistency(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> None:
    """Fail closed if presentation drifts from the typed final contract."""
    packet_list = list(packets)
    text = str(report or "")
    errors: list[str] = []

    effective_date = _effective_report_date(packet_list)
    if effective_date and not re.search(
        rf"(?m)^# (?:AI )?美股(?:综合|决策)日报 · {re.escape(effective_date)}\s*$",
        text,
    ):
        errors.append(f"report: effective trade date drift expected={effective_date}")

    for packet in packet_list:
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
        expected_authorized = (
            f"**当前执行授权**：**{'是' if packet.assessment.execution_authorized else '否'}**"
        )
        expected_position = (
            f"**当前可执行仓位上限**：**{_execution_position_pct(packet):.1f}%**"
        )
        if expected_verdict not in section:
            errors.append(f"{packet.symbol}: final verdict drift")
        if expected_action not in section:
            errors.append(f"{packet.symbol}: final action drift")
        if expected_agreement not in section:
            errors.append(f"{packet.symbol}: fusion agreement drift")
        if expected_authorized not in section:
            errors.append(f"{packet.symbol}: execution authorization drift")
        if expected_position not in section:
            errors.append(f"{packet.symbol}: executable position drift")
        if "当前执行 **" in section:
            errors.append(f"{packet.symbol}: upstream V4 action masquerades as final execution")
        if packet.execution.has_active_plan and not packet.assessment.execution_authorized:
            conditional = (
                f"**条件触发后最大仓位上限**：**"
                f"{100.0 * float(packet.execution.max_position_pct):.1f}%**"
            )
            if conditional not in section:
                errors.append(f"{packet.symbol}: conditional position cap missing")
            if "**V6 最大仓位上限**" in section:
                errors.append(f"{packet.symbol}: unauthorized plan rendered as active")
    if errors:
        raise ValueError("FinalDecisionPacket/report mismatch: " + "; ".join(errors))


def apply_final_decision_contract(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> str:
    """Normalize presentation from FinalDecisionPacket and validate it.

    Business decisions are never repaired or re-derived from Markdown. Only
    presentation fields that must mirror the authoritative packet are
    normalized, then the report is validated fail-closed.
    """
    packet_list = list(packets)
    text = _normalize_report_presentation(str(report or ""), packet_list)
    assert_final_decision_report_consistency(text, packet_list)
    return text
