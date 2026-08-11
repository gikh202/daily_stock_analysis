from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .final_decision_service import build_final_decision_packets
from .fusion_contracts import (
    FinalDecisionPacket,
    action_label_zh,
    agreement_label_zh,
    direction_label_zh,
    render_final_decision_lines,
)
from .v4_research_adapter import latest_v4_views


# Keep the underlying V6 schema stable; localization is presentation-only.
_TEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("## 1. Market Pulse", "## 1. 市场脉搏"),
    ("## 2. Significant Changes", "## 2. 重要变化"),
    ("## 3. Opportunity Ranking", "## 3. 机会排名"),
    ("## 4. Setup Cards", "## 4. 交易计划卡"),
    ("## 5. LLM Health", "## 5. 大模型健康度"),
    ("## 6. Prediction Scoreboard", "## 6. 预测验证看板"),
    ("## 7. Free Public Data Context", "## 7. 免费公共数据"),
    ("## 8. Run Health", "## 8. 运行健康度"),
    ("## Methodology", "## 方法说明"),
    ("### Macro snapshot", "### 宏观快照"),
    ("### Recent SEC filings", "### 近期 SEC 文件"),
    ("Regime:", "市场状态:"),
    ("Breadth:", "市场广度:"),
    ("Average Opportunity:", "平均机会分:"),
    ("Average Risk:", "平均风险分:"),
    ("Evidence Coverage:", "证据覆盖率:"),
    ("Opportunity / Quality / Risk:", "机会 / 质量 / 风险:"),
    ("Forecast Score:", "预测评分:"),
    ("Factors:", "因子:"),
    ("Entry:", "入场区间:"),
    ("Stop:", "止损:"),
    ("Targets:", "目标位:"),
    ("R:R:", "风险收益比:"),
    ("No V6 signals available.", "暂无可用的 V6 信号。"),
)

_TABLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "| Rank | Symbol | Decision | Direction | Forecast | Opportunity | Quality | Risk | Evidence | LLM |",
        "| 排名 | 标的 | 决策 | 方向 | 预测分 | 机会分 | 质量分 | 风险分 | 证据 | LLM |",
    ),
    (
        "| Horizon | N | Direction Hit | BUY N | BUY Hit | Avoid N | Avoid Hit | Forecast IC | Opportunity IC |",
        "| 周期 | 样本数 | 方向命中率 | 买入样本 | 买入命中率 | 回避样本 | 回避命中率 | 预测 IC | 机会 IC |",
    ),
)

_TOKEN_MAP = {
    "BUY_SETUP": "买入准备",
    "WATCH": "观察",
    "WAIT": "等待",
    "AVOID": "回避",
    "bullish": "看多",
    "neutral": "中性",
    "bearish": "看空",
    "healthy": "正常",
    "fallback": "已回退",
    "degraded": "降级",
    "unknown": "未知",
    "missing": "缺失",
    "insufficient_data": "数据不足",
    "measurable": "可衡量",
    "risk_on": "风险偏好开启",
    "risk_off": "风险偏好关闭",
    "broad": "广泛",
    "narrow": "狭窄",
}

_DECISION_LABEL = {
    "BUY_SETUP": "买入准备",
    "WATCH": "观察",
    "WAIT": "等待",
    "AVOID": "回避",
}
_LLM_LABEL = {
    "healthy": "正常",
    "fallback": "已回退",
    "degraded": "降级",
    "unknown": "未知",
    "missing": "缺失",
}


def translate_v6_markdown_to_chinese(markdown: str) -> str:
    """Translate legacy V6 human-facing Markdown without mutating data fields."""
    text = str(markdown or "")
    for old, new in _TABLE_REPLACEMENTS:
        text = text.replace(old, new)
    for old, new in _TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    for token, translated in _TOKEN_MAP.items():
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            translated,
            text,
        )
    return text


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number(value: Any, digits: int = 1) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:.{digits}f}"


def _pct01(value: Any) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{100.0 * number:.0f}%"


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(items: Iterable[Any], *, limit: int = 6) -> list[str]:
    result: list[str] = []
    normalized: set[str] = set()
    for raw in items:
        item = _text(raw)
        if not item:
            continue
        key = re.sub(r"\s+", " ", item).strip().lower()
        key = re.sub(r"\[e\d+\]", "", key).strip()
        if not key or key in normalized:
            continue
        normalized.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _latest_v4_views(
    records: Optional[Sequence[Mapping[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Compatibility alias; V4 normalization now has one implementation."""
    return latest_v4_views(records)


def count_v4_structured_records(
    records: Optional[Sequence[Mapping[str, Any]]],
) -> int:
    return len(latest_v4_views(records))


def _direction_label(value: Any) -> str:
    return direction_label_zh(_text(value))


def _decision_label(value: Any) -> str:
    normalized = _text(value).upper()
    return _DECISION_LABEL.get(normalized, _text(value) or "等待")


def _feature_line(features: Mapping[str, Any]) -> str:
    labels = (
        ("trend", "趋势"),
        ("momentum", "动量"),
        ("relative_strength", "相对强弱"),
        ("volume_confirmation", "量能"),
        ("fundamental_quality", "基本面"),
        ("market_regime", "市场状态"),
    )
    return " | ".join(
        f"{label} {_number(features.get(key), 0)}" for key, label in labels
    )


def _is_non_trading(v4: Mapping[str, Any]) -> bool:
    if v4.get("is_trading_day") is False:
        return True
    return _text(v4.get("phase")).lower() in {"non_trading", "closed"}


def _format_plan(v6: Mapping[str, Any], v4: Mapping[str, Any]) -> list[str]:
    plan = _mapping(v6.get("trade_plan"))
    sniper = _mapping(v4.get("sniper_points"))
    strategy = _mapping(v4.get("position_strategy"))
    lines: list[str] = []
    entry = plan.get("entry_zone")
    stop = plan.get("stop_loss")
    targets = plan.get("targets")
    rr = plan.get("risk_reward")
    max_position = _finite(plan.get("max_position_pct"))

    if entry:
        lines.append(f"- **融合入场区间**: `{entry}`（优先采用 V6 确定性风控计划）")
    elif sniper.get("ideal_buy"):
        lines.append(f"- **参考入场**: {sniper.get('ideal_buy')}")
    if stop is not None:
        lines.append(f"- **止损/失效位**: `{stop}`")
    elif sniper.get("stop_loss"):
        lines.append(f"- **止损/失效位**: {sniper.get('stop_loss')}")
    if targets:
        lines.append(f"- **目标位**: `{targets}`")
    elif sniper.get("take_profit"):
        lines.append(f"- **目标位**: {sniper.get('take_profit')}")
    if rr is not None:
        lines.append(f"- **风险收益比**: `{rr}R`")
    if max_position is not None and max_position > 0:
        lines.append(f"- **V6 最大仓位上限**: `{100.0 * max_position:.1f}%`")
    elif strategy.get("suggested_position"):
        lines.append(f"- **V4 仓位参考**: {strategy.get('suggested_position')}")

    v4_refs = [
        sniper.get("ideal_buy"),
        sniper.get("secondary_buy"),
        sniper.get("stop_loss"),
        sniper.get("take_profit"),
    ]
    compact = "；".join(_text(item) for item in v4_refs if _text(item))
    if compact and (entry or stop is not None or targets):
        lines.append(f"- **V4 价格参考**: {compact}")
    if strategy.get("risk_control"):
        lines.append(f"- **风险控制**: {strategy.get('risk_control')}")
    return lines


def _market_conclusion(
    pulse: Mapping[str, Any],
    v4_views: Mapping[str, Mapping[str, Any]],
) -> str:
    regime = _text(pulse.get("regime")).lower()
    breadth = _text(pulse.get("breadth")).lower()
    avg_risk = _finite(pulse.get("average_risk"))
    if regime == "risk_on" and breadth == "broad":
        base = "市场风险偏好与广度共同偏多，环境对多头更友好"
    elif regime == "risk_off":
        base = "市场处于风险规避状态，应优先控制回撤"
    else:
        base = "市场环境缺少单边共振，应以标的级确认信号为主"
    if avg_risk is not None and avg_risk >= 60:
        base += "；但组合平均风险分偏高，暂不适合激进扩仓"
    if any(_is_non_trading(view) for view in v4_views.values()):
        base += "。本次为非交易时段复盘，所有入场建议均需下个交易日确认"
    return base + "。"


def _typed_fusion_summary(
    packet: FinalDecisionPacket,
    v4: Mapping[str, Any],
) -> str:
    """Explain a typed decision without deriving a second decision."""
    parts = [
        f"V4 {packet.v4_horizon or '10d'}预测{direction_label_zh(packet.v4_direction)}",
    ]
    if packet.v4_expected_return_pct is not None:
        parts.append(f"预期收益{packet.v4_expected_return_pct:+.1f}%")
    parts.append(f"V6确定性方向{direction_label_zh(packet.v6_direction)}")
    parts.append(
        f"机会分{_number(packet.opportunity_score)}、风险分{_number(packet.risk_score)}"
    )

    agreement = agreement_label_zh(packet)
    if agreement == "方向一致":
        alignment = "两层方向一致。"
    elif agreement == "部分一致":
        alignment = "方向尚未形成完全共振。"
    elif agreement == "方向分歧":
        alignment = "两层方向存在直接分歧，最终执行状态以 typed 风控合同为准。"
    elif agreement == "共同偏中性":
        alignment = "两层共同偏中性，等待新的方向性证据。"
    else:
        alignment = "V4结构化数据缺失，当前不能形成最终跨层买入判断。"

    summary = "，".join(parts) + "，" + alignment
    immediate = _text(v4.get("immediate_action"))
    if packet.non_trading:
        summary += " 当前为非交易时段，只形成下一交易日计划，不等同于立即下单。"
    elif immediate:
        summary += f" V4执行护栏：{immediate}"
    return summary


def _render_deltas(deltas: Sequence[Mapping[str, Any]]) -> list[str]:
    meaningful: list[Mapping[str, Any]] = []
    for item in deltas:
        changed = (
            item.get("decision_before") != item.get("decision_after")
            or item.get("direction_before") != item.get("direction_after")
        )
        magnitudes = [
            _finite(item.get(name)) or 0.0
            for name in ("opportunity_delta", "risk_delta", "forecast_delta")
        ]
        if changed or max(abs(v) for v in magnitudes) >= 5.0:
            meaningful.append(item)
    if not meaningful:
        return ["- 本轮没有达到 5 分变化阈值的显著变化，或这是该标的首次 V6 记录。"]

    result: list[str] = []
    for item in meaningful[:10]:
        result.append(
            "- **{code}**: 决策 {before} → {after} | 方向 {d_before} → {d_after} | 机会 {opp:+.1f} | 风险 {risk:+.1f} | 预测 {forecast:+.1f}".format(
                code=_text(item.get("code")) or "-",
                before=_decision_label(item.get("decision_before")),
                after=_decision_label(item.get("decision_after")),
                d_before=_direction_label(item.get("direction_before")),
                d_after=_direction_label(item.get("direction_after")),
                opp=_finite(item.get("opportunity_delta")) or 0.0,
                risk=_finite(item.get("risk_delta")) or 0.0,
                forecast=_finite(item.get("forecast_delta")) or 0.0,
            )
        )
    return result


def _render_public_context(context: Mapping[str, Any]) -> list[str]:
    lines = [
        "## 5. 宏观与官方数据",
        "",
        "> SEC/FRED 当前只作为证据与背景，不直接修改 V6 数值评分。",
        "",
    ]
    status = _mapping(context.get("status"))
    if not status or not status.get("enabled"):
        lines.extend(["- 免费公共数据增强：**已关闭**", ""])
        return lines

    fred = _mapping(context.get("fred"))
    if fred:
        lines.extend(["### 宏观快照", ""])
        labels = {
            "DGS10": "美国10年期国债收益率",
            "DGS2": "美国2年期国债收益率",
            "BAMLH0A0HYM2": "美国高收益债期权调整利差",
            "VIXCLS": "VIX",
        }
        for series_id, item in fred.items():
            item_map = _mapping(item)
            latest = _mapping(item_map.get("latest"))
            label = labels.get(series_id, _text(item_map.get("label")) or series_id)
            if latest.get("value"):
                lines.append(
                    f"- {label}: **{latest.get('value')}**（{latest.get('date') or '-'}）"
                )
            elif item_map.get("error"):
                lines.append(f"- {label}: 暂不可用")
        lines.append("")

    sec = _mapping(context.get("sec"))
    if sec:
        lines.extend(["### 近期 SEC 文件", ""])
        for code, item in sorted(sec.items()):
            item_map = _mapping(item)
            filings = item_map.get("recent_filings") or []
            compact: list[str] = []
            for filing in filings[:4] if isinstance(filings, list) else []:
                filing_map = _mapping(filing)
                if filing_map:
                    compact.append(
                        f"{filing_map.get('form') or '-'} {filing_map.get('filing_date') or '-'}"
                    )
            if compact:
                lines.append(f"- **{code}**: " + "；".join(compact))
        lines.append("")
    return lines


def _render_scoreboard(scoreboard: Mapping[str, Any]) -> list[str]:
    status = _text(scoreboard.get("status")) or "insufficient_data"
    label = _TOKEN_MAP.get(status, status)
    lines = [
        "## 6. 预测验证看板",
        "",
        f"- 状态：**{label}**",
        f"- 每个周期最小样本门槛：**{scoreboard.get('minimum_samples', 50)}**",
        "- 样本不足时不把命中率或相关性包装成已验证胜率。",
        "",
        "| 周期 | 样本数 | 方向命中率 | 买入样本 | 买入命中率 | 回避样本 | 回避命中率 | 预测 IC | 机会 IC |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    horizons = scoreboard.get("horizons") or []
    if isinstance(horizons, list) and horizons:
        for item in horizons:
            row = _mapping(item)

            def p(name: str) -> str:
                value = _finite(row.get(name))
                return "N/A" if value is None else f"{value:.1f}%"

            lines.append(
                "| {h}D | {n} | {hit} | {buy_n} | {buy_hit} | {avoid_n} | {avoid_hit} | {fic} | {oic} |".format(
                    h=row.get("horizon_days", "-"),
                    n=row.get("samples", 0),
                    hit=p("directional_hit_rate_pct"),
                    buy_n=row.get("buy_setup_samples", 0),
                    buy_hit=p("buy_setup_hit_rate_pct"),
                    avoid_n=row.get("avoidance_samples", 0),
                    avoid_hit=p("avoidance_hit_rate_pct"),
                    fic=_number(row.get("forecast_score_ic_spearman"), 4),
                    oic=_number(row.get("opportunity_ic_spearman"), 4),
                )
            )
    else:
        lines.append("| - | 0 | N/A | 0 | N/A | 0 | N/A | N/A | N/A |")
    lines.append("")
    return lines


def render_integrated_chinese_report(
    payload: Mapping[str, Any],
    *,
    v4_records: Optional[Sequence[Mapping[str, Any]]] = None,
    report_date: Optional[str] = None,
) -> str:
    """Render the Chinese report from the typed V4+V6 final decision contract.

    FinalDecisionPacket is the only source allowed to decide final action,
    cross-layer agreement, buy-worthiness and execution authorization. This
    renderer may explain those fields, but it does not re-derive them.
    """
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    board = [
        dict(item)
        for item in (payload.get("board") or [])
        if isinstance(item, Mapping)
    ]
    pulse = _mapping(payload.get("market_pulse"))
    v4_views = latest_v4_views(v4_records)
    packets = build_final_decision_packets(payload, v4_records=v4_records)
    packet_by_symbol = {packet.symbol: packet for packet in packets if packet.symbol}

    lines = [
        f"# AI 美股综合日报 · {report_date}",
        "",
        "> V4 提供 AI 投研、新闻、基本面、技术面和阶段上下文；V6 提供确定性方向、机会/质量/风险评分和风控计划。最终动作、是否值得买、执行授权与 V4/V6 融合状态只读取 FinalDecisionPacket；报告层不再自行推导第二套结论。",
        "",
        "## 1. 今日最终总览",
        "",
        f"- 市场状态：**{_TOKEN_MAP.get(_text(pulse.get('regime')), _text(pulse.get('regime')) or '未知')}**",
        f"- 市场广度：**{_TOKEN_MAP.get(_text(pulse.get('breadth')), _text(pulse.get('breadth')) or '未知')}**",
        f"- V6 平均机会分 / 风险分：**{_number(pulse.get('average_opportunity'))} / {_number(pulse.get('average_risk'))}**",
        f"- 平均证据覆盖率：**{_pct01(pulse.get('average_evidence_coverage'))}**",
        f"- **综合判断**：{_market_conclusion(pulse, v4_views)}",
        "",
        "### 最终优先级",
        "",
        "| 排名 | 标的 | 最终动作 | 融合状态 | V4预测 | V6方向 | V6预测分 | 机会分 | 风险分 | 证据 |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|",
    ]

    fusion_rows: list[tuple[Dict[str, Any], Dict[str, Any], FinalDecisionPacket]] = []
    for rank, item in enumerate(board, 1):
        code = _text(item.get("code")).upper()
        packet = packet_by_symbol.get(code)
        if packet is None:
            continue
        v4 = v4_views.get(code, {})
        fusion_rows.append((item, v4, packet))
        v4_view = "缺失"
        if packet.fusion_complete:
            v4_view = f"{packet.v4_horizon or '10d'} {direction_label_zh(packet.v4_direction)}"
        lines.append(
            "| {rank} | {code} | {action} | {agreement} | {v4_view} | {v6_dir} | {forecast} | {opp} | {risk} | {evidence} |".format(
                rank=rank,
                code=code or "-",
                action=action_label_zh(packet),
                agreement=agreement_label_zh(packet),
                v4_view=v4_view,
                v6_dir=direction_label_zh(packet.v6_direction),
                forecast=_number(packet.v6_forecast_score),
                opp=_number(packet.opportunity_score),
                risk=_number(packet.risk_score),
                evidence=_pct01(packet.evidence_coverage),
            )
        )
    if not board:
        lines.append("| - | - | 等待 | 数据不足 | - | 中性 | N/A | N/A | N/A | 0% |")

    lines.extend(["", "## 2. 今日变化", ""])
    lines.extend(_render_deltas(payload.get("deltas") or []))
    lines.extend(["", "## 3. 标的融合分析", ""])

    for index, (item, v4, packet) in enumerate(fusion_rows, 1):
        code = packet.symbol
        name = _text(v4.get("name")) if v4 else ""
        title = f"### {index}. {code}"
        if name and name.upper() != code:
            title += f" · {name}"
        title += (
            f" · 最终：{action_label_zh(packet)} · {agreement_label_zh(packet)}"
        )
        lines.extend([title, ""])

        lines.append(
            f"- **最终结论**：**{action_label_zh(packet)}**。{_typed_fusion_summary(packet, v4)}"
        )
        lines.extend(render_final_decision_lines(packet))

        if v4.get("analysis_summary"):
            lines.append(
                f"- **V4 投研摘要（原始观点）**：{v4.get('analysis_summary')}"
            )

        lines.append(
            f"- **V6 确定性视角**：方向 **{direction_label_zh(packet.v6_direction)}** | 预测分 **{_number(packet.v6_forecast_score)}** | 机会/质量/风险 **{_number(packet.opportunity_score)}/{_number(item.get('quality_score'))}/{_number(packet.risk_score)}** | 证据 **{_pct01(packet.evidence_coverage)}**"
        )
        features = _mapping(item.get("features"))
        if features:
            lines.append(f"- **V6 因子**：{_feature_line(features)}")

        if v4:
            forecast = _mapping(v4.get("forecast"))
            forecast_bits = [
                f"{forecast.get('horizon') or '10d'} **{_direction_label(forecast.get('direction'))}**",
            ]
            if forecast.get("expected_return_pct") is not None:
                forecast_bits.append(
                    f"预期收益 **{float(forecast['expected_return_pct']):+.1f}%**"
                )
            if forecast.get("up_probability") is not None:
                forecast_bits.append(
                    f"模型上行概率 **{float(forecast['up_probability']):.0f}%（未校准）**"
                )
            forecast_bits.append(f"当前执行 **{v4.get('operation') or '未知'}**")
            lines.append("- **预测层 vs 执行层**：" + " | ".join(forecast_bits))
            if forecast.get("rationale"):
                lines.append(f"  - V4 预测依据：{forecast.get('rationale')}")

            drivers = _dedupe(
                [
                    v4.get("strongest_bullish"),
                    v4.get("earnings_outlook"),
                    v4.get("latest_news"),
                    *v4.get("catalysts", []),
                    *item.get("catalysts", []),
                ],
                limit=5,
            )
            if drivers:
                lines.append("- **核心驱动/催化**：")
                lines.extend(f"  - {value}" for value in drivers)

            risks = _dedupe(
                [
                    v4.get("strongest_bearish"),
                    *v4.get("risks", []),
                    *item.get("risks", []),
                    v4.get("risk_warning"),
                ],
                limit=5,
            )
            if risks:
                lines.append("- **主要风险**：")
                lines.extend(f"  - {value}" for value in risks)

            if v4.get("technical_analysis"):
                lines.append(f"- **技术面**：{v4.get('technical_analysis')}")
            if v4.get("volume_analysis"):
                lines.append(f"- **量价确认**：{v4.get('volume_analysis')}")
            if v4.get("fundamental_analysis"):
                lines.append(f"- **基本面**：{v4.get('fundamental_analysis')}")
            if v4.get("sentiment_summary") or v4.get("news_summary"):
                lines.append(
                    f"- **舆情/新闻**：{v4.get('sentiment_summary') or v4.get('news_summary')}"
                )

        plan_lines = _format_plan(item, v4)
        if plan_lines:
            lines.append("- **融合交易计划**：")
            lines.extend("  " + value for value in plan_lines)

        if v4:
            watch = _dedupe(v4.get("watch_conditions", []), limit=5)
            if watch:
                lines.append("- **下一次确认条件**：")
                lines.extend(f"  - {value}" for value in watch)
            if v4.get("next_check_time"):
                lines.append(f"  - 下次检查：**{v4.get('next_check_time')}**")

        limitations = _dedupe(
            [*item.get("limitations", []), *v4.get("data_limitations", [])]
            if v4
            else item.get("limitations", []),
            limit=5,
        )
        if limitations:
            lines.append("- **数据限制**：" + "；".join(limitations))
        lines.append("")

    lines.extend(["## 4. 大模型与数据健康度", ""])
    health = Counter(
        _text(item.get("llm_health")) or "unknown" for item in board
    )
    if health:
        lines.append(
            "- V4/上游 LLM 状态："
            + "，".join(
                f"{_LLM_LABEL.get(key, key)} {value}" for key, value in health.items()
            )
        )
    else:
        lines.append("- 暂无可用 LLM 健康度记录。")
    lines.append(f"- V4 结构化投研记录参与融合：**{len(v4_views)}** 个标的")
    lines.append(
        "- FinalDecisionPacket 是最终动作、买入价值、融合状态和执行授权的唯一事实源；报告层只展示证据与上下文。"
    )
    lines.append("")

    lines.extend(_render_public_context(_mapping(payload.get("public_context"))))
    lines.extend(_render_scoreboard(_mapping(payload.get("scoreboard"))))

    run = _mapping(payload.get("run"))
    lines.extend(
        [
            "## 7. 运行健康度",
            "",
            f"- 新增 V6 信号：**{run.get('new_signals', 0)}**",
            f"- 跳过已存在信号：**{run.get('skipped_existing', 0)}**",
            f"- 跳过不可用记录：**{run.get('skipped_unusable', 0)}**",
            f"- 新增成熟结果：**{run.get('new_outcomes', 0)}**",
            f"- 尚未成熟结果：**{run.get('not_yet_mature', 0)}**",
            f"- SQLite 完整性检查：**{run.get('quick_check', 'unknown')}**",
            "",
            "## 方法说明",
            "",
            "- V4 负责新闻、事件、基本面、技术解释、预测叙事和市场阶段上下文。",
            "- V6 负责确定性方向、机会/质量/风险、证据覆盖、排序和数值风控。",
            "- FinalDecisionPacket 是 V4+V6 最终融合的唯一业务合同；unified_report 不再维护 `_final_action`、`_agreement` 或 `_decision_balance_lines` 等平行业务规则。",
            "- “是否值得买”与“当前是否允许执行”是独立字段：条件式可买可以保留买入价值，但不会提前获得执行授权。",
            "- 看多与看空证据同时保留；风险闸门可以限制执行，但不能删除另一侧有效证据。",
            "- V4 的 up_probability 属于模型预测字段，目前未做真实概率校准，因此仅作参考，不当作真实胜率。",
            "- 缺失证据保持缺失；SEC/FRED 在通过样本外验证前仅作背景证据。",
            "",
            f"*生成器：V6 typed 融合报告层 · {payload.get('version', 'v6')} · {payload.get('generated_at', '-')}*",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_unified_chinese_report(
    v6_markdown: str,
    v4_markdown: Optional[str] = None,
    *,
    v6_payload: Optional[Mapping[str, Any]] = None,
    v4_records: Optional[Sequence[Mapping[str, Any]]] = None,
    report_date: Optional[str] = None,
) -> str:
    """Build the single final Chinese report.

    Normal production uses structured V4 analysis_history records + the V6 JSON
    payload and typed FinalDecisionPacket. Markdown arguments remain only for a
    backward-compatible fallback; V4 Markdown is never appended wholesale.
    """
    if v6_payload is not None:
        return render_integrated_chinese_report(
            v6_payload,
            v4_records=v4_records,
            report_date=report_date,
        )

    fallback = translate_v6_markdown_to_chinese(v6_markdown).rstrip()
    if v4_markdown:
        fallback += (
            "\n\n> ⚠️ 本次未取得可用于逐标的融合的结构化 V4 数据。为避免再次出现简单拼接，"
            "V4 原始 Markdown 未附加；请检查生产数据库/Artifact 恢复链路。"
        )
    return fallback + "\n"
