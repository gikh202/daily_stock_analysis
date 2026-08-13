from __future__ import annotations

import re
from typing import Iterable, Sequence

from .fusion_contracts import (
    FinalDecisionPacket,
    action_label_zh,
    agreement_label_zh,
    render_final_decision_lines,
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
_UPSTREAM_CURRENT_EXECUTION_RE = re.compile(
    r"当前执行(?:\s*[:：])?\s*\*\*[^*\n]+\*\*"
)
_AUTHORITY_PREFIXES = (
    "- **是否值得买**",
    "- **当前执行授权**",
    "- **当前可执行仓位上限**",
    "- **条件触发后最大仓位上限**",
)


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


def _authoritative_decision_lines(packet: FinalDecisionPacket) -> list[str]:
    """Render the authority block directly from the typed final contract."""
    typed_lines = render_final_decision_lines(packet)
    verdict_line = typed_lines[0] if typed_lines else (
        f"- **是否值得买**：**{verdict_label_zh(packet)}**"
    )
    return [verdict_line, *_execution_authorization_lines(packet)]


def _assert_existing_authority_compatible(
    section: str,
    packet: FinalDecisionPacket,
) -> None:
    """Refuse to silently repair a stale business verdict or execution state.

    The renderer may add missing typed fields and collapse duplicate *matching*
    fields, but a pre-existing value that disagrees with FinalDecisionPacket is
    a contract violation and must fail closed.
    """
    expected_verdict = f"**是否值得买**：**{verdict_label_zh(packet)}**"
    expected_authorized = (
        f"**当前执行授权**：**{'是' if packet.assessment.execution_authorized else '否'}**"
    )
    expected_position = (
        f"**当前可执行仓位上限**：**{_execution_position_pct(packet):.1f}%**"
    )
    expected_conditional = None
    if packet.execution.has_active_plan and not packet.assessment.execution_authorized:
        expected_conditional = (
            f"**条件触发后最大仓位上限**：**"
            f"{100.0 * float(packet.execution.max_position_pct):.1f}%**"
        )

    errors: list[str] = []
    for line in section.splitlines():
        if line.startswith("- **是否值得买**") and expected_verdict not in line:
            errors.append(f"{packet.symbol}: final verdict drift")
        elif line.startswith("- **当前执行授权**") and expected_authorized not in line:
            errors.append(f"{packet.symbol}: execution authorization drift")
        elif line.startswith("- **当前可执行仓位上限**") and expected_position not in line:
            errors.append(f"{packet.symbol}: executable position drift")
        elif line.startswith("- **条件触发后最大仓位上限**"):
            if expected_conditional is None or expected_conditional not in line:
                errors.append(f"{packet.symbol}: conditional position cap drift")

    if errors:
        raise ValueError("FinalDecisionPacket/report mismatch: " + "; ".join(errors))


def _replace_authoritative_decision_block(
    section: str,
    packet: FinalDecisionPacket,
) -> str:
    """Rebuild matching/missing authority fields from the typed contract."""
    trailing_newline = "\n" if section.endswith("\n") else ""
    lines = section.splitlines()
    kept: list[str] = []
    insertion_index: int | None = None
    for line in lines:
        if any(line.startswith(prefix) for prefix in _AUTHORITY_PREFIXES):
            if insertion_index is None:
                insertion_index = len(kept)
            continue
        kept.append(line)

    if insertion_index is None:
        insertion_index = 1 if kept else 0

    authority = _authoritative_decision_lines(packet)
    kept[insertion_index:insertion_index] = authority
    return "\n".join(kept) + trailing_newline


def _news_evidence_unavailable(section: str) -> bool:
    lowered = section.lower()
    return any(marker.lower() in lowered for marker in _NEWS_EVIDENCE_UNAVAILABLE)


def _normalize_news_presentation(section: str) -> str:
    """Fail safe when current news evidence is unavailable."""
    if not _news_evidence_unavailable(section):
        return section
    text = _NEWS_LINE_RE.sub(
        "- **舆情/新闻**：近期新闻证据不足，无法可靠判断当前舆情方向或是否存在新增催化/利空。",
        section,
    )
    return text.replace("暂无利好催化", "近期催化证据不足")


def _normalize_us_price_units(section: str) -> str:
    """Render bare CNY-style stock price suffixes as USD in U.S. stock cards."""
    return _US_PRICE_YUAN_RE.sub(r"$\1", section)


def _normalize_next_check_presentation(section: str) -> str:
    """Canonicalize the next live confirmation checkpoint to 09:45 ET."""
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
    """Normalize display without silently changing a stale business decision."""
    _assert_existing_authority_compatible(section, packet)

    text = _normalize_news_presentation(section)
    text = _normalize_us_price_units(text)
    text = _normalize_next_check_presentation(text)

    text = text.replace("**预测层 vs 执行层**", "**预测层 vs 上游投研层**")
    if packet.v4_operation:
        text = _UPSTREAM_CURRENT_EXECUTION_RE.sub(
            f"上游投研动作 **{packet.v4_operation}**（非最终执行）",
            text,
        )
    else:
        text = _UPSTREAM_CURRENT_EXECUTION_RE.sub(
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

    text = _replace_authoritative_decision_block(text, packet)

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
        if section.count(expected_verdict) != 1:
            errors.append(f"{packet.symbol}: final verdict drift/duplicate")
        if expected_action not in section:
            errors.append(f"{packet.symbol}: final action drift")
        if expected_agreement not in section:
            errors.append(f"{packet.symbol}: fusion agreement drift")
        if section.count(expected_authorized) != 1:
            errors.append(f"{packet.symbol}: execution authorization drift/duplicate")
        if section.count(expected_position) != 1:
            errors.append(f"{packet.symbol}: executable position drift/duplicate")
        if "当前执行 **" in section or "当前执行：**" in section or "当前执行:**" in section:
            errors.append(f"{packet.symbol}: upstream V4 action masquerades as final execution")
        if packet.execution.has_active_plan and not packet.assessment.execution_authorized:
            conditional = (
                f"**条件触发后最大仓位上限**：**"
                f"{100.0 * float(packet.execution.max_position_pct):.1f}%**"
            )
            if section.count(conditional) != 1:
                errors.append(f"{packet.symbol}: conditional position cap missing/duplicate")
            if "**V6 最大仓位上限**" in section:
                errors.append(f"{packet.symbol}: unauthorized plan rendered as active")
        elif "**条件触发后最大仓位上限**" in section:
            errors.append(f"{packet.symbol}: stale conditional position cap")
    if errors:
        raise ValueError("FinalDecisionPacket/report mismatch: " + "; ".join(errors))


def apply_final_decision_contract(
    report: str,
    packets: Iterable[FinalDecisionPacket],
) -> str:
    """Normalize display, preserving fail-closed typed business semantics."""
    packet_list = list(packets)
    text = _normalize_report_presentation(str(report or ""), packet_list)
    assert_final_decision_report_consistency(text, packet_list)
    return text
