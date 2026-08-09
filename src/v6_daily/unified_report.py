from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


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
    ("| Rank | Symbol | Decision | Direction | Forecast | Opportunity | Quality | Risk | Evidence | LLM |", "| 排名 | 标的 | 决策 | 方向 | 预测分 | 机会分 | 质量分 | 风险分 | 证据 | LLM |"),
    ("| Horizon | N | Direction Hit | BUY N | BUY Hit | Avoid N | Avoid Hit | Forecast IC | Opportunity IC |", "| 周期 | 样本数 | 方向命中率 | 买入样本 | 买入命中率 | 回避样本 | 回避命中率 | 预测 IC | 机会 IC |"),
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

_DIRECTION_LABEL = {"bullish": "看多", "neutral": "中性", "bearish": "看空"}
_DECISION_LABEL = {"BUY_SETUP": "买入准备", "WATCH": "观察", "WAIT": "等待", "AVOID": "回避"}
_LLM_LABEL = {"healthy": "正常", "fallback": "已回退", "degraded": "降级", "unknown": "未知", "missing": "缺失"}


def translate_v6_markdown_to_chinese(markdown: str) -> str:
    """Translate legacy V6 human-facing Markdown without mutating data fields."""
    text = str(markdown or "")
    for old, new in _TABLE_REPLACEMENTS:
        text = text.replace(old, new)
    for old, new in _TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    for token, translated in _TOKEN_MAP.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", translated, text)
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


def _parse_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _texts(value: Any, *, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        candidate = _text(item)
        if candidate and candidate not in result:
            result.append(candidate)
        if len(result) >= limit:
            break
    return result


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


def _normalize_direction(value: Any) -> str:
    text = _text(value).lower()
    if text in {"bullish", "看多", "强烈看多", "偏多", "上涨"}:
        return "bullish"
    if text in {"bearish", "看空", "强烈看空", "偏空", "下跌"}:
        return "bearish"
    if text in {"neutral", "中性", "震荡", "横盘"}:
        return "neutral"
    if "看多" in text or "bull" in text:
        return "bullish"
    if "看空" in text or "bear" in text:
        return "bearish"
    return "neutral"


def _v4_forecast(raw: Mapping[str, Any]) -> Dict[str, Any]:
    forecast = _mapping(raw.get("forecast")) or _mapping(_mapping(raw.get("dashboard")).get("forecast"))
    horizon = _text(forecast.get("primary_horizon")) or "10d"
    block = _mapping(_mapping(forecast.get("horizons")).get(horizon))
    direction = _normalize_direction(block.get("direction") or raw.get("trend_prediction"))
    return {
        "horizon": horizon,
        "direction": direction,
        "up_probability": _finite(block.get("up_probability")),
        "expected_return_pct": _finite(block.get("expected_return_pct")),
        "confidence": _text(block.get("confidence")),
        "rationale": _text(block.get("rationale")),
    }


def _latest_v4_views(records: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, tuple[int, Mapping[str, Any]]] = {}
    for record in records or ():
        code = _text(record.get("code")).upper()
        if not code:
            continue
        try:
            history_id = int(record.get("id") or 0)
        except (TypeError, ValueError):
            history_id = 0
        previous = latest.get(code)
        if previous is None or history_id >= previous[0]:
            latest[code] = (history_id, record)

    views: Dict[str, Dict[str, Any]] = {}
    for code, (history_id, record) in latest.items():
        raw = _parse_object(record.get("raw_result"))
        if not raw:
            continue
        dashboard = _mapping(raw.get("dashboard"))
        core = _mapping(dashboard.get("core_conclusion"))
        intel = _mapping(dashboard.get("intelligence"))
        battle = _mapping(dashboard.get("battle_plan"))
        phase = _mapping(dashboard.get("phase_decision"))
        attr = _mapping(dashboard.get("signal_attribution"))
        data = _mapping(dashboard.get("data_perspective"))
        execution = _mapping(raw.get("execution")) or _mapping(dashboard.get("execution"))
        phase_context = _mapping(phase.get("phase_context"))
        view = {
            "history_id": history_id,
            "code": code,
            "name": _text(raw.get("name")) or code,
            "score": _finite(raw.get("sentiment_score") or dashboard.get("sentiment_score")),
            "operation": _text(execution.get("operation_advice") or raw.get("operation_advice")),
            "execution_action": _text(execution.get("action") or raw.get("action")),
            "trend_prediction": _text(raw.get("trend_prediction")),
            "forecast": _v4_forecast(raw),
            "one_sentence": _text(core.get("one_sentence")),
            "position_advice": _mapping(core.get("position_advice")),
            "analysis_summary": _text(raw.get("analysis_summary")),
            "technical_analysis": _text(raw.get("technical_analysis")),
            "fundamental_analysis": _text(raw.get("fundamental_analysis")),
            "volume_analysis": _text(raw.get("volume_analysis")),
            "news_summary": _text(raw.get("news_summary")),
            "risk_warning": _text(raw.get("risk_warning")),
            "earnings_outlook": _text(intel.get("earnings_outlook")),
            "sentiment_summary": _text(intel.get("sentiment_summary")),
            "latest_news": _text(intel.get("latest_news")),
            "catalysts": _texts(intel.get("positive_catalysts")),
            "risks": _texts(intel.get("risk_alerts")),
            "sniper_points": _mapping(battle.get("sniper_points")),
            "position_strategy": _mapping(battle.get("position_strategy")),
            "watch_conditions": _texts(phase.get("watch_conditions")),
            "next_check_time": _text(phase.get("next_check_time")),
            "immediate_action": _text(phase.get("immediate_action")),
            "data_limitations": _texts(phase.get("data_limitations")),
            "strongest_bullish": _text(attr.get("strongest_bullish_signal")),
            "strongest_bearish": _text(attr.get("strongest_bearish_signal")),
            "phase": _text(phase_context.get("phase")),
            "is_trading_day": phase_context.get("is_trading_day"),
            "effective_daily_bar_date": _text(phase_context.get("effective_daily_bar_date")),
            "data_perspective": data,
        }
        views[code] = view
    return views


def count_v4_structured_records(records: Optional[Sequence[Mapping[str, Any]]]) -> int:
    return len(_latest_v4_views(records))


def _direction_label(value: Any) -> str:
    return _DIRECTION_LABEL.get(_normalize_direction(value), "中性")


def _decision_label(value: Any) -> str:
    normalized = _text(value).upper()
    return _DECISION_LABEL.get(normalized, _text(value) or "等待")


def _agreement(v6_direction: Any, v4_direction: Any) -> str:
    left = _normalize_direction(v6_direction)
    right = _normalize_direction(v4_direction)
    if left == right and left != "neutral":
        return "方向一致"
    if left == right:
        return "共同偏中性"
    if "neutral" in {left, right}:
        return "部分一致"
    return "方向分歧"


def _is_non_trading(v4: Mapping[str, Any]) -> bool:
    if v4.get("is_trading_day") is False:
        return True
    return _text(v4.get("phase")).lower() in {"non_trading", "closed"}


def _final_action(v6: Mapping[str, Any], v4: Mapping[str, Any], agreement: str) -> str:
    decision = _text(v6.get("decision")).upper()
    if decision == "AVOID":
        return "回避"
    if decision == "WAIT":
        return "等待"
    if decision == "WATCH":
        return "观察"
    if decision == "BUY_SETUP":
        if agreement == "方向分歧":
            return "观察"
        if _text(v4.get("operation")) in {"观望", "卖出", "减仓"} or _is_non_trading(v4):
            return "观察（等待确认）"
        return "买入准备"
    return _decision_label(decision)


def _fusion_reason(v6: Mapping[str, Any], v4: Mapping[str, Any], agreement: str, final_action: str) -> str:
    forecast = _mapping(v4.get("forecast"))
    v4_dir = _direction_label(forecast.get("direction"))
    v6_dir = _direction_label(v6.get("direction"))
    horizon = _text(forecast.get("horizon")) or "10d"
    expected = _finite(forecast.get("expected_return_pct"))
    v4_phrase = f"V4 {horizon}预测{v4_dir}"
    if expected is not None:
        v4_phrase += f"、预期收益{expected:+.1f}%"
    v6_phrase = f"V6确定性方向{v6_dir}，机会分{_number(v6.get('opportunity_score'))}、风险分{_number(v6.get('risk_score'))}"

    if agreement == "方向一致":
        reason = f"{v4_phrase}，{v6_phrase}，两层方向一致。"
    elif agreement == "部分一致":
        reason = f"{v4_phrase}，但{v6_phrase}，方向尚未形成完全共振。"
    elif agreement == "方向分歧":
        reason = f"{v4_phrase}，而{v6_phrase}，出现方向分歧，按风险优先原则不升级仓位。"
    else:
        reason = f"{v4_phrase}，{v6_phrase}。"

    immediate = _text(v4.get("immediate_action"))
    if _is_non_trading(v4):
        reason += " 当前为非交易时段，最终只形成下一交易日计划，不等同于立即下单。"
    elif immediate:
        reason += f" V4执行护栏：{immediate}"
    return f"**{final_action}**。{reason}"


def _feature_line(features: Mapping[str, Any]) -> str:
    labels = (
        ("trend", "趋势"),
        ("momentum", "动量"),
        ("relative_strength", "相对强弱"),
        ("volume_confirmation", "量能"),
        ("fundamental_quality", "基本面"),
        ("market_regime", "市场状态"),
    )
    return " | ".join(f"{label} {_number(features.get(key), 0)}" for key, label in labels)


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

    v4_refs = [sniper.get("ideal_buy"), sniper.get("secondary_buy"), sniper.get("stop_loss"), sniper.get("take_profit")]
    compact = "；".join(_text(item) for item in v4_refs if _text(item))
    if compact and (entry or stop is not None or targets):
        lines.append(f"- **V4 价格参考**: {compact}")
    if strategy.get("risk_control"):
        lines.append(f"- **风险控制**: {strategy.get('risk_control')}")
    return lines


def _market_conclusion(pulse: Mapping[str, Any], v4_views: Mapping[str, Mapping[str, Any]]) -> str:
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


def _render_deltas(deltas: Sequence[Mapping[str, Any]]) -> list[str]:
    meaningful: list[Mapping[str, Any]] = []
    for item in deltas:
        changed = item.get("decision_before") != item.get("decision_after") or item.get("direction_before") != item.get("direction_after")
        magnitudes = [_finite(item.get(name)) or 0.0 for name in ("opportunity_delta", "risk_delta", "forecast_delta")]
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
    lines = ["## 5. 宏观与官方数据", "", "> SEC/FRED 当前只作为证据与背景，不直接修改 V6 数值评分。", ""]
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
                lines.append(f"- {label}: **{latest.get('value')}**（{latest.get('date') or '-'}）")
            elif item_map.get("error"):
                lines.append(f"- {label}: 暂不可用")
        lines.append("")

    sec = _mapping(context.get("sec"))
    if sec:
        lines.extend(["### 近期 SEC 文件", ""])
        for code, item in sorted(sec.items()):
            item_map = _mapping(item)
            filings = item_map.get("recent_filings") or []
            compact = []
            for filing in filings[:4] if isinstance(filings, list) else []:
                filing_map = _mapping(filing)
                if filing_map:
                    compact.append(f"{filing_map.get('form') or '-'} {filing_map.get('filing_date') or '-'}")
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
            item = _mapping(item)
            def p(name: str) -> str:
                v = _finite(item.get(name))
                return "N/A" if v is None else f"{v:.1f}%"
            lines.append(
                "| {h}D | {n} | {hit} | {buy_n} | {buy_hit} | {avoid_n} | {avoid_hit} | {fic} | {oic} |".format(
                    h=item.get("horizon_days", "-"),
                    n=item.get("samples", 0),
                    hit=p("directional_hit_rate_pct"),
                    buy_n=item.get("buy_setup_samples", 0),
                    buy_hit=p("buy_setup_hit_rate_pct"),
                    avoid_n=item.get("avoidance_samples", 0),
                    avoid_hit=p("avoidance_hit_rate_pct"),
                    fic=_number(item.get("forecast_score_ic_spearman"), 4),
                    oic=_number(item.get("opportunity_ic_spearman"), 4),
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
    """Render one coherent report by reconciling V4 research with V6 risk logic.

    V4 contributes qualitative research, forecast narrative, news, fundamentals,
    technical interpretation and phase-aware execution context. V6 owns the
    deterministic ranking/risk layer. Conflicts are surfaced and can only make
    the final action more conservative; V4 prose never upgrades V6 risk gates.
    """
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    board = [dict(item) for item in (payload.get("board") or []) if isinstance(item, dict)]
    pulse = _mapping(payload.get("market_pulse"))
    v4_views = _latest_v4_views(v4_records)

    lines = [
        f"# AI 美股综合日报 · {report_date}",
        "",
        "> 本报告不是 V4 与 V6 的原文拼接：V4 提供 AI 投研、新闻、基本面、技术面和执行护栏；V6 提供确定性方向、机会/质量/风险评分、证据覆盖与风控计划。最终结论按“风险优先、冲突降级、缺失不补分”规则融合。",
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

    fusion_rows: list[tuple[Dict[str, Any], Dict[str, Any], str, str]] = []
    for rank, item in enumerate(board, 1):
        code = _text(item.get("code")).upper()
        v4 = v4_views.get(code, {})
        v4_forecast = _mapping(v4.get("forecast"))
        v4_direction = v4_forecast.get("direction") or v4.get("trend_prediction") or "neutral"
        agreement = _agreement(item.get("direction"), v4_direction) if v4 else "V4结构化数据缺失"
        action = _final_action(item, v4, agreement) if v4 else _decision_label(item.get("decision"))
        fusion_rows.append((item, v4, agreement, action))
        v4_view = _direction_label(v4_direction) if v4 else "缺失"
        horizon = _text(v4_forecast.get("horizon")) if v4 else ""
        if horizon and v4_view != "缺失":
            v4_view = f"{horizon} {v4_view}"
        lines.append(
            "| {rank} | {code} | {action} | {agreement} | {v4_view} | {v6_dir} | {forecast} | {opp} | {risk} | {evidence} |".format(
                rank=rank,
                code=code or "-",
                action=action,
                agreement=agreement,
                v4_view=v4_view,
                v6_dir=_direction_label(item.get("direction")),
                forecast=_number(item.get("forecast_score")),
                opp=_number(item.get("opportunity_score")),
                risk=_number(item.get("risk_score")),
                evidence=_pct01(item.get("evidence_coverage")),
            )
        )
    if not board:
        lines.append("| - | - | 等待 | 数据不足 | - | 中性 | N/A | N/A | N/A | 0% |")

    lines.extend(["", "## 2. 今日变化", ""])
    lines.extend(_render_deltas(payload.get("deltas") or []))
    lines.extend(["", "## 3. 标的融合分析", ""])

    for index, (item, v4, agreement, action) in enumerate(fusion_rows, 1):
        code = _text(item.get("code")).upper()
        name = _text(v4.get("name")) if v4 else ""
        title = f"### {index}. {code}"
        if name and name.upper() != code:
            title += f" · {name}"
        title += f" · 最终：{action} · {agreement}"
        lines.extend([title, ""])

        if v4:
            lines.append(f"- **最终结论**：{_fusion_reason(item, v4, agreement, action)}")
            if v4.get("analysis_summary"):
                lines.append(f"- **V4 投研摘要**：{v4.get('analysis_summary')}")
        else:
            lines.append(f"- **最终结论**：仅获得 V6 结构化信号，当前按 V6 决策 **{action}**；V4 结构化投研记录缺失，不补造定性证据。")

        lines.append(
            f"- **V6 确定性视角**：方向 **{_direction_label(item.get('direction'))}** | 预测分 **{_number(item.get('forecast_score'))}** | 机会/质量/风险 **{_number(item.get('opportunity_score'))}/{_number(item.get('quality_score'))}/{_number(item.get('risk_score'))}** | 证据 **{_pct01(item.get('evidence_coverage'))}**"
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
                forecast_bits.append(f"预期收益 **{float(forecast['expected_return_pct']):+.1f}%**")
            if forecast.get("up_probability") is not None:
                forecast_bits.append(f"模型上行概率 **{float(forecast['up_probability']):.0f}%（未校准）**")
            forecast_bits.append(f"当前执行 **{v4.get('operation') or '未知'}**")
            lines.append("- **预测层 vs 执行层**：" + " | ".join(forecast_bits))
            if forecast.get("rationale"):
                lines.append(f"  - V4 预测依据：{forecast.get('rationale')}")

            drivers = _dedupe(
                [v4.get("strongest_bullish"), v4.get("earnings_outlook"), v4.get("latest_news"), *v4.get("catalysts", []), *item.get("catalysts", [])],
                limit=5,
            )
            if drivers:
                lines.append("- **核心驱动/催化**：")
                lines.extend(f"  - {value}" for value in drivers)

            risks = _dedupe(
                [v4.get("strongest_bearish"), *v4.get("risks", []), *item.get("risks", []), v4.get("risk_warning")],
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
                lines.append(f"- **舆情/新闻**：{v4.get('sentiment_summary') or v4.get('news_summary')}")

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

        limitations = _dedupe([*item.get("limitations", []), *v4.get("data_limitations", [])] if v4 else item.get("limitations", []), limit=5)
        if limitations:
            lines.append("- **数据限制**：" + "；".join(limitations))
        lines.append("")

    lines.extend(["## 4. 大模型与数据健康度", ""])
    health = Counter(_text(item.get("llm_health")) or "unknown" for item in board)
    if health:
        lines.append("- V4/上游 LLM 状态：" + "，".join(f"{_LLM_LABEL.get(key, key)} {value}" for key, value in health.items()))
    else:
        lines.append("- 暂无可用 LLM 健康度记录。")
    lines.append(f"- V4 结构化投研记录参与融合：**{len(v4_views)}** 个标的")
    lines.append("- V4 定性内容不会直接改写 V6 数值评分；出现冲突时只允许维持或降低行动等级。")
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
            "- V4 负责新闻、事件、基本面、技术解释、预测叙事和市场阶段护栏。",
            "- V6 负责确定性方向、机会/质量/风险、证据覆盖、排序和数值风控。",
            "- 最终报告逐标的比较两层方向与执行状态：一致则保留，分歧则明确展示并降级，不进行原文拼接。",
            "- V4 的 up_probability 属于模型预测字段，目前未做真实概率校准，因此仅作参考，不当作真实胜率。",
            "- 缺失证据保持缺失；SEC/FRED 在通过样本外验证前仅作背景证据。",
            "",
            f"*生成器：V6 融合报告层 · {payload.get('version', 'v6')} · {payload.get('generated_at', '-')}*",
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
    payload and therefore performs semantic reconciliation rather than text
    concatenation. Markdown arguments remain only for backward-compatible
    fallback; the V4 Markdown is never appended wholesale.
    """
    if v6_payload is not None:
        return render_integrated_chinese_report(v6_payload, v4_records=v4_records, report_date=report_date)

    fallback = translate_v6_markdown_to_chinese(v6_markdown).rstrip()
    if v4_markdown:
        fallback += (
            "\n\n> ⚠️ 本次未取得可用于逐标的融合的结构化 V4 数据。为避免再次出现简单拼接，"
            "V4 原始 Markdown 未附加；请检查生产数据库/Artifact 恢复链路。"
        )
    return fallback + "\n"
