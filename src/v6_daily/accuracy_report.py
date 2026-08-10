from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional, Sequence

from .unified_report import build_unified_chinese_report as _build_v60_report


_DIRECTION = {"bullish": "看多", "neutral": "中性", "bearish": "看空"}
_TYPE = {"STOCK": "个股", "ETF": "ETF"}


_EMAIL_SUBJECT_META_RE = re.compile(r"^\[dsa-email-subject\]:\s+#\s+\(([^)\n]+)\)\s*$", re.MULTILINE)
_CHASE_SUFFIX_OR_BOUNDARY = r"(?:高|涨|价|买|(?=$|[\s，,。；;、但]))"
_CHASE_TARGET = rf"追{_CHASE_SUFFIX_OR_BOUNDARY}"
_CHASE_PATTERN = rf"(?:日内)?(?:可|可以){_CHASE_TARGET}"
_NEGATED_CHASE_GAP = (
    r"(?:(?!(?:但|并且|同时|以及|然后|然而|不过|可是|却|而且|且|而|或者|或))"
    r"[^，,。；;\n]){0,20}?"
)
_NEGATED_CHASE_PATTERN = (
    rf"(?:不可以|不可|不应|不得|禁止|严禁|不要|不宜|切勿|勿|别)"
    rf"{_NEGATED_CHASE_GAP}{_CHASE_TARGET}"
    rf"|不\s*{_CHASE_TARGET}"
)
_PRICE_NUMBER_PATTERN = (
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:\s*[-–—~～至]\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?"
)
_PRICE_YUAN_RE = re.compile(
    rf"(?<![\d.,])\$?({_PRICE_NUMBER_PATTERN})\s*元"
)
_NON_YUAN_PRICE_AMOUNT_RE = re.compile(
    rf"(?:\$\s*{_PRICE_NUMBER_PATTERN}|"
    rf"{_PRICE_NUMBER_PATTERN}\s*(?:美元|美金|USD\b)|"
    rf"USD\s*{_PRICE_NUMBER_PATTERN})",
    re.IGNORECASE,
)
_MAX_POSITION_RE = re.compile(
    r"\*\*最大仓位上限\*\*[:：]\s*`?\s*(\d+(?:\.\d+)?)\s*%"
)
_PLAN_PRICE_LABEL_RE = re.compile(
    r"\*\*(?:融合入场区间|保留入场区间（当前不可执行）|参考入场|辅助入场参考（非执行）|"
    r"止损/失效位|目标位|价格参考|辅助价格参考（非执行）)\*\*"
)
_RISK_CONTROL_LABEL_RE = re.compile(
    r"\*\*(?:风险控制|辅助风险控制（非执行）)\*\*"
)
_WATCH_PRICE_CONTEXT_RE = re.compile(
    r"(?:价格|股价|现价|收盘价|开盘价|入场|买入|买点|卖点|止损|止盈|目标|"
    r"支撑|压力|突破|上破|下破|跌破|站上|守住|回踩|高开|低开|价位|点位|区间)"
)
_WATCH_PRICE_BARRIER_RE = re.compile(
    r"(?:[，,。；;\n]|并且|同时|以及|然后|但|且|而|或|并|与)"
)


def _num(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _horizon_cell(item: Mapping[str, Any], key: str) -> str:
    horizons = item.get("horizon_forecasts")
    block = horizons.get(key) if isinstance(horizons, dict) else None
    if not isinstance(block, dict):
        return "N/A"
    direction = _DIRECTION.get(str(block.get("direction") or "neutral"), "中性")
    score = _num(block.get("score"))
    coverage = block.get("evidence_coverage")
    try:
        coverage_text = f"{100.0 * float(coverage):.0f}%"
    except (TypeError, ValueError):
        coverage_text = "N/A"
    return f"{direction} {score}（因子覆盖{coverage_text}）"


def _accuracy_section(payload: Mapping[str, Any]) -> str:
    board = [item for item in (payload.get("board") or []) if isinstance(item, dict)]
    lines = [
        "## V6.1 多周期确定性预测",
        "",
        "> 5D、10D、20D 使用不同的确定性权重；10D 仍作为兼容主预测。分数不是胜率，真实概率只会在历史样本达到门槛后校准。",
        "",
        "| 标的 | 类型 | 5D | 10D | 20D | 机会分 | 风险分 |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for item in board:
        instrument = str(item.get("instrument_type") or "STOCK").upper()
        lines.append(
            "| {code} | {kind} | {h5} | {h10} | {h20} | {opp} | {risk} |".format(
                code=item.get("code") or "-",
                kind=_TYPE.get(instrument, instrument),
                h5=_horizon_cell(item, "5d"),
                h10=_horizon_cell(item, "10d"),
                h20=_horizon_cell(item, "20d"),
                opp=_num(item.get("opportunity_score")),
                risk=_num(item.get("risk_score")),
            )
        )
    if not board:
        lines.append("| - | - | N/A | N/A | N/A | N/A | N/A |")

    context = payload.get("public_context") or {}
    fred = context.get("fred") if isinstance(context, dict) else None
    derived = fred.get("derived") if isinstance(fred, dict) else None
    if isinstance(derived, dict):
        lines.extend(
            [
                "",
                "### FRED 宏观风险",
                "",
                f"- 宏观风险分：**{_num(derived.get('macro_risk_score'))}**",
                f"- 10Y-2Y 利差：**{_num(derived.get('yield_curve_10y_2y'), 3)}**",
                f"- 10Y 最近5个观测变化：**{_num(derived.get('dgs10_change_5obs'), 3)}**",
                f"- 高收益债利差最近5个观测变化：**{_num(derived.get('hy_oas_change_5obs'), 3)}**",
                f"- VIX 最近5个观测变化：**{_num(derived.get('vix_change_5obs'), 2)}**",
            ]
        )

    sec = context.get("sec") if isinstance(context, dict) else None
    fundamentals = []
    if isinstance(sec, dict):
        for code, raw in sorted(sec.items()):
            item = raw if isinstance(raw, dict) else {}
            fundamental = item.get("fundamentals") if isinstance(item.get("fundamentals"), dict) else None
            if fundamental and fundamental.get("quality_score") is not None:
                fundamentals.append((code, fundamental))
    if fundamentals:
        lines.extend(["", "### SEC CompanyFacts 基本面", ""])
        for code, item in fundamentals:
            lines.append(
                "- **{code}**：质量分 **{quality}**，营收同比 **{revenue}%**，经营利润率 **{margin}%**，FCF 利润率 **{fcf}%**，数据覆盖 **{coverage}%**。".format(
                    code=code,
                    quality=_num(item.get("quality_score")),
                    revenue=_num(item.get("revenue_yoy_pct")),
                    margin=_num(item.get("operating_margin_pct")),
                    fcf=_num(item.get("fcf_margin_pct")),
                    coverage=_num(100.0 * float(item.get("coverage") or 0.0), 0),
                )
            )
    return "\n".join(lines) + "\n"


def _priority_rows(report: str) -> list[list[str]]:
    match = re.search(
        r"(?ms)^### 最终优先级\s*\n\s*"
        r"(?P<table>\| 排名 .*?)(?=^## 2\. 今日变化|^## V6\.1|^## 3\.)",
        report,
    )
    if not match:
        return []
    rows: list[list[str]] = []
    for line in match.group("table").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped or "排名" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 10:
            rows.append(cells)
    return rows


def _compact_priority_table(rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return ""
    lines = [
        "### 今日动作",
        "",
        "| 标的 | 动作 | 主预测 | 量化方向 | 机会 | 风险 |",
        "|---|---|---|---|---:|---:|",
    ]
    for cells in rows:
        lines.append(
            f"| {cells[1]} | {cells[2]} | {cells[4]} | {cells[5]} | {cells[7]} | {cells[8]} |"
        )
    return "\n".join(lines)


def _email_subject(report: str, rows: Sequence[Sequence[str]]) -> str:
    date_match = re.search(r"^# .*?·\s*(\d{4}-\d{2}-\d{2})\s*$", report, re.MULTILINE)
    date_text = date_match.group(1) if date_match else ""
    date_short = date_text[5:] if date_text else ""
    highlights = [f"{cells[1]} {cells[2]}" for cells in rows[:2]]
    middle = " · ".join(highlights)
    if middle and date_short:
        return f"美股决策日报｜{middle}｜{date_short}"
    if middle:
        return f"美股决策日报｜{middle}"
    if date_text:
        return f"美股决策日报｜{date_text}"
    return "美股决策日报"


def _compact_validation(section: str) -> str:
    status_match = re.search(r"状态：\*\*([^*]+)\*\*", section)
    minimum_match = re.search(r"最小样本门槛：\*\*(\d+)\*\*", section)
    status = status_match.group(1).strip() if status_match else "数据不足"
    minimum = minimum_match.group(1) if minimum_match else "50"

    rows: list[tuple[str, str, str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped or "周期" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 3 and cells[0].endswith("D"):
            rows.append((cells[0], cells[1], cells[2]))

    has_samples = any(str(sample).strip() not in {"", "0"} for _, sample, _ in rows)
    if not has_samples:
        return ""

    lines = [
        "## 预测可信度",
        "",
        f"- 当前：**{status}**；每个周期至少 **{minimum}** 个成熟样本后再判断稳定命中率。",
        "",
        "| 周期 | 成熟样本 | 方向命中率 |",
        "|---:|---:|---:|",
    ]
    for horizon, sample, hit in rows:
        lines.append(f"| {horizon} | {sample} | {hit} |")
    return "\n".join(lines)


def _normalize_us_investor_terms(text: str) -> str:
    """Normalize table coverage and U.S. timezone labels without touching prose."""
    normalized: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            line = re.sub(
                r"（证据(\d+(?:\.\d+)?%|N/A)）",
                r"（因子覆盖\1）",
                line,
            )
        line = re.sub(r"\((?:EDT|EST|美东时间)\)", "ET（美东）", line)
        line = re.sub(r"（(?:EDT|EST|美东时间)）", "ET（美东）", line)
        line = re.sub(r"\b(?:EDT|EST)\b|美东时间", "ET（美东）", line)
        normalized.append(line)
    return "\n".join(normalized)


def _normalize_execution_price_yuan(line: str) -> str:
    """Convert a legacy yuan suffix when its line is known to describe stock price."""
    return _PRICE_YUAN_RE.sub(r"$\1", line)


def _normalize_watch_price_yuan(line: str) -> str:
    """Convert only yuan amounts locally bound to a stock-price watch phrase."""
    matches = list(_PRICE_YUAN_RE.finditer(line))
    if not matches:
        return line

    output: list[str] = []
    cursor = 0
    previous_amount_end = 0
    for match in matches:
        prefix = line[previous_amount_end : match.start()]
        boundary_end = 0
        for boundary in _WATCH_PRICE_BARRIER_RE.finditer(prefix):
            boundary_end = max(boundary_end, boundary.end())
        for amount in _NON_YUAN_PRICE_AMOUNT_RE.finditer(prefix):
            boundary_end = max(boundary_end, amount.end())
        local_prefix = prefix[boundary_end:]

        output.append(line[cursor : match.start()])
        if _WATCH_PRICE_CONTEXT_RE.search(local_prefix):
            output.append(f"${match.group(1)}")
        else:
            output.append(match.group(0))
        cursor = match.end()
        previous_amount_end = match.end()

    output.append(line[cursor:])
    return "".join(output)


def _rewrite_affirmative_chase_clauses(line: str) -> str:
    """Rewrite only the affirmative chase token and preserve all trailing qualifiers."""
    protected: Dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        key = f"__DSA_NO_CHASE_{len(protected)}__"
        protected[key] = match.group(0)
        return key

    masked = re.sub(_NEGATED_CHASE_PATTERN, protect, line)
    masked = re.sub(_CHASE_PATTERN, "仅视为强势确认，不追价", masked)
    for key, value in protected.items():
        masked = masked.replace(key, value)
    return masked


def _has_positive_max_position(section: str) -> bool:
    # The upstream plan renderer emits this marker only when the raw position
    # allowance is strictly positive. A tiny positive cap can display as 0.0%
    # after one-decimal rounding, so marker presence is the source of truth.
    return bool(_MAX_POSITION_RE.search(section))


def _standardize_stock_card(section: str) -> str:
    """Make one investor card use one execution-price hierarchy."""
    has_deterministic_levels = "**融合入场区间**" in section
    has_deterministic_plan = has_deterministic_levels and _has_positive_max_position(section)
    inactive_deterministic_levels = has_deterministic_levels and not has_deterministic_plan
    no_chase_guard = bool(re.search(_NEGATED_CHASE_PATTERN, section))
    output: list[str] = []
    execution_note_added = False
    price_block: Optional[str] = None

    for line in section.splitlines():
        top_label = re.match(r"^-\s+\*\*([^*]+)\*\*[:：]", line)
        if top_label:
            label = top_label.group(1)
            if label in {"交易计划", "下一次确认条件"}:
                price_block = label
            else:
                price_block = None

        # Uncalibrated model probability is useful in raw research artifacts but
        # must not look like a live win probability in the investor inbox.
        line = re.sub(
            r"\s*\|\s*模型上行概率\s+\*\*[^*\n]*未校准[^*\n]*\*\*",
            "",
            line,
        )

        if "量化视角" in line:
            line = line.replace("| 证据 **", "| 总体证据覆盖 **")

        # A positive deterministic position allowance is required before V6
        # levels are presented as executable. Legacy V4 price/position/risk
        # instructions are suppressed only for an active deterministic plan.
        if has_deterministic_plan and re.match(
            r"^\s*-\s+\*\*(?:价格参考|仓位参考|风险控制)\*\*[:：]", line
        ):
            continue

        if not has_deterministic_plan:
            line = line.replace("**价格参考**", "**辅助价格参考（非执行）**")
            line = line.replace("**仓位参考**", "**辅助仓位参考（非执行）**")
            line = line.replace("**风险控制**", "**辅助风险控制（非执行）**")
            line = line.replace("**参考入场**", "**辅助入场参考（非执行）**")

        # Remove an obvious A-share turnover template that has no valid place in
        # a U.S. investor email. The raw report remains available for audit.
        if "亿级别成交额" in line:
            continue

        if no_chase_guard:
            line = _rewrite_affirmative_chase_clauses(line)

        if has_deterministic_plan:
            line = line.replace(
                "（优先采用 确定性风控计划）",
                "（唯一执行价格口径）",
            )
        elif inactive_deterministic_levels:
            line = line.replace(
                "**融合入场区间**",
                "**保留入场区间（当前不可执行）**",
            )
            line = line.replace(
                "（优先采用 确定性风控计划）",
                "（组合风控当前禁止新仓）",
            )

        if re.match(r"^\s*-\s+\*\*交易计划\*\*[:：]\s*$", line):
            if inactive_deterministic_levels:
                line = line.replace("**交易计划**", "**交易计划（当前不可执行）**")
            elif not has_deterministic_levels:
                line = line.replace("**交易计划**", "**辅助交易计划（未触发）**")

        if price_block == "交易计划":
            if _RISK_CONTROL_LABEL_RE.search(line):
                line = _normalize_watch_price_yuan(line)
            elif _PLAN_PRICE_LABEL_RE.search(line):
                line = _normalize_execution_price_yuan(line)
        elif price_block == "下一次确认条件":
            line = _normalize_watch_price_yuan(line)

        output.append(line)

        if (
            has_deterministic_plan
            and not execution_note_added
            and re.match(r"^\s*-\s+\*\*交易计划\*\*[:：]\s*$", line)
        ):
            output.append(
                "  - **执行口径**: 确定性风控计划为唯一执行价格口径；"
                "投研层价格仅用于解释，不作为下单依据。"
            )
            execution_note_added = True

    return "\n".join(output)


def _standardize_stock_cards(text: str) -> str:
    pattern = re.compile(
        r"(?ms)^### \d+\. .*?(?=^### \d+\.|^## \d+\.|^## 预测可信度|\Z)"
    )
    return pattern.sub(lambda match: _standardize_stock_card(match.group(0)), text)


def build_investor_email_markdown(report: str) -> str:
    """Convert the full V6 research report into a concise stock-only email view.

    The full Markdown/JSON artifacts keep diagnostics, source health and validation
    details. The investor email keeps one execution hierarchy: daily action and a
    deterministic V6 trade plan are authoritative only when a positive position
    allowance makes the plan active; V4 narrative remains explanatory.
    """
    text = str(report or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    rows = _priority_rows(text)
    subject = _email_subject(text, rows)

    # Remove the implementation-oriented opening paragraph; the body should read
    # like an investment brief rather than a system architecture document.
    text = re.sub(r"(?m)^> 本报告不是 V4 与 V6.*\n?", "", text)
    text = text.replace("# AI 美股综合日报", "# 美股决策日报")
    text = text.replace("## 1. 今日最终总览", "## 今日概览")
    text = text.replace("- V6 平均机会分 / 风险分：", "- 平均机会分 / 风险分：")
    text = re.sub(r"(?m)^- 平均证据覆盖率：.*\n?", "", text)

    # Replace the very wide implementation table with a mobile-friendly action table.
    priority_match = re.search(
        r"(?ms)^### 最终优先级\s*\n\s*"
        r"\| 排名 .*?(?=^## 2\. 今日变化|^## V6\.1|^## 3\.)",
        text,
    )
    compact_priority = _compact_priority_table(rows)
    if priority_match and compact_priority:
        text = text[: priority_match.start()] + compact_priority + "\n\n" + text[priority_match.end() :]

    # Hide a no-op change section; keep it only when something actually changed.
    changes = re.search(r"(?ms)^## 2\. 今日变化\s*\n(?P<body>.*?)(?=^## )", text)
    if changes:
        if "本轮没有达到 5 分变化阈值" in changes.group("body"):
            text = text[: changes.start()] + text[changes.end() :]
        else:
            replacement = changes.group(0).replace("## 2. 今日变化", "## 较上次变化", 1)
            text = text[: changes.start()] + replacement + text[changes.end() :]

    text = text.replace("## V6.1 多周期确定性预测", "## 多周期预测")
    text = re.sub(
        r"(?m)^> 5D、10D、20D 使用不同的确定性权重；.*$",
        "> 5D / 10D / 20D 分别观察短、中、稍长周期；分数与因子覆盖率用于相对比较，均不直接等同于胜率或概率。",
        text,
    )

    # Raw macro/source diagnostics remain in JSON artifacts, not in the inbox.
    text = re.sub(
        r"(?ms)^### FRED 宏观风险\s*\n.*?(?=^### SEC CompanyFacts 基本面|^## 3\.)",
        "",
        text,
    )
    text = re.sub(
        r"(?ms)^### SEC CompanyFacts 基本面\s*\n.*?(?=^## 3\.)",
        "",
        text,
    )

    text = text.replace("## 3. 标的融合分析", "## 标的详解")
    text = text.replace(
        "## 标的详解",
        "## 标的详解\n\n> 执行优先级：今日动作优先；仅当确定性交易计划具有正数最大仓位上限时，"
        "其价格口径才可执行并优先。组合风控将最大仓位压至 0 时，保留价位不可执行，"
        "不能覆盖其他上下文；投研摘要仅用于解释。",
        1,
    )
    replacements = (
        ("V4 投研摘要", "投研摘要"),
        ("V6 确定性视角", "量化视角"),
        ("V6 因子", "关键因子"),
        ("V4 预测依据", "预测依据"),
        ("融合交易计划", "交易计划"),
        ("V6 最大仓位上限", "最大仓位上限"),
        ("V4 仓位参考", "仓位参考"),
        ("V4 价格参考", "价格参考"),
        ("V6确定性方向", "量化方向"),
        ("V4执行护栏", "执行护栏"),
        ("V4 ", ""),
        ("V6 ", ""),
    )
    for old, new in replacements:
        text = text.replace(old, new)

    text = _standardize_stock_cards(text)
    text = _normalize_us_investor_terms(text)

    # Remove operational/model/source-health sections from email.
    text = re.sub(
        r"(?ms)^## 4\. 大模型与数据健康度\s*\n.*?(?=^## 6\. 预测验证看板)",
        "",
        text,
    )

    scoreboard = re.search(
        r"(?ms)^## 6\. 预测验证看板\s*\n(?P<body>.*?)(?=^## 7\. 运行健康度|\Z)",
        text,
    )
    if scoreboard:
        compact = _compact_validation(scoreboard.group("body"))
        text = text[: scoreboard.start()] + (compact + "\n\n" if compact else "") + text[scoreboard.end() :]

    text = re.sub(r"(?ms)^## 7\. 运行健康度\s*\n.*\Z", "", text)

    # Remove any implementation-only limitation line that may have survived
    # inside a stock card. Stock-specific data/price limitations are preserved.
    filtered: list[str] = []
    technical_markers = (
        "LLM",
        "SQLite",
        "SEC/FRED",
        "SEC CompanyFacts",
        "FRED ",
        "数值评分",
        "生成器：",
        "engine_version",
    )
    for line in text.splitlines():
        if any(marker in line for marker in technical_markers):
            continue
        filtered.append(line)
    text = "\n".join(filtered)

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text += (
        "\n\n---\n\n"
        "> 风险提示：本邮件用于研究与交易计划，不构成自动下单指令；"
        "非交易时段的入场、止损和目标位需在下一交易日结合最新价格重新确认。"
    )
    return f"[dsa-email-subject]: # ({subject})\n{text}\n"


def extract_investor_email_subject(markdown: str) -> Optional[str]:
    """Read the hidden subject generated by ``build_investor_email_markdown``."""
    match = _EMAIL_SUBJECT_META_RE.search(str(markdown or ""))
    return match.group(1).strip() if match else None


def build_accuracy_unified_report(
    v6_markdown: str,
    v4_markdown: Optional[str] = None,
    *,
    v6_payload: Optional[Dict[str, Any]] = None,
    v4_records: Optional[Sequence[Mapping[str, Any]]] = None,
    report_date: Optional[str] = None,
) -> str:
    payload = v6_payload or {}
    report = _build_v60_report(
        v6_markdown,
        v4_markdown,
        v6_payload=payload,
        v4_records=v4_records,
        report_date=report_date,
    )
    report = report.replace(
        "> SEC/FRED 当前只作为证据与背景，不直接修改 V6 数值评分。",
        "> V6.1 会把 SEC CompanyFacts 的确定性基本面质量和 FRED 宏观风险作为结构化数值证据；自由文本和 LLM 主观分数仍然不会直接进入评分。",
    )
    report = report.replace(
        "SEC/FRED 仅作为证据与背景，不直接修改 V6 数值评分",
        "SEC CompanyFacts/FRED 作为结构化数值证据；LLM 自由文本不直接修改 V6 数值评分",
    )
    section = _accuracy_section(payload)
    marker = "## 3. 标的融合分析"
    if marker in report:
        report = report.replace(marker, section + "\n" + marker, 1)
    else:
        report = report.rstrip() + "\n\n" + section
    return report