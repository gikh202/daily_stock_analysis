from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .unified_report import build_unified_chinese_report as _build_v60_report


_DIRECTION = {"bullish": "看多", "neutral": "中性", "bearish": "看空"}
_TYPE = {"STOCK": "个股", "ETF": "ETF"}


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
    return f"{direction} {score}（证据{coverage_text}）"


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
