from __future__ import annotations

import json
import math
from typing import Any, Dict, Mapping, Optional, Sequence


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _texts(value: Any, *, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _parse_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = _text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_direction(value: Any) -> str:
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


def normalize_v4_forecast(raw: Mapping[str, Any]) -> Dict[str, Any]:
    dashboard = _mapping(raw.get("dashboard"))
    forecast = _mapping(raw.get("forecast")) or _mapping(dashboard.get("forecast"))
    horizon = _text(forecast.get("primary_horizon")) or "10d"
    block = _mapping(_mapping(forecast.get("horizons")).get(horizon))
    return {
        "horizon": horizon,
        "direction": normalize_direction(block.get("direction") or raw.get("trend_prediction")),
        "up_probability": _finite(block.get("up_probability")),
        "expected_return_pct": _finite(block.get("expected_return_pct")),
        "confidence": _text(block.get("confidence")),
        "rationale": _text(block.get("rationale")),
    }


def normalize_v4_record(record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    code = _text(record.get("code")).upper()
    raw = _parse_object(record.get("raw_result"))
    if not code or not raw:
        return None
    try:
        history_id = int(record.get("id") or 0)
    except (TypeError, ValueError):
        history_id = 0

    dashboard = _mapping(raw.get("dashboard"))
    core = _mapping(dashboard.get("core_conclusion"))
    intel = _mapping(dashboard.get("intelligence"))
    battle = _mapping(dashboard.get("battle_plan"))
    phase = _mapping(dashboard.get("phase_decision"))
    attr = _mapping(dashboard.get("signal_attribution"))
    data = _mapping(dashboard.get("data_perspective"))
    execution = _mapping(raw.get("execution")) or _mapping(dashboard.get("execution"))
    phase_context = _mapping(phase.get("phase_context"))

    return {
        "history_id": history_id,
        "code": code,
        "name": _text(raw.get("name")) or code,
        "score": _finite(raw.get("sentiment_score") or dashboard.get("sentiment_score")),
        "operation": _text(execution.get("operation_advice") or raw.get("operation_advice")),
        "execution_action": _text(execution.get("action") or raw.get("action")),
        "trend_prediction": _text(raw.get("trend_prediction")),
        "forecast": normalize_v4_forecast(raw),
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


def latest_v4_views(records: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for record in records or ():
        normalized = normalize_v4_record(record)
        if not normalized:
            continue
        code = str(normalized["code"])
        current = latest.get(code)
        if current is None or int(normalized.get("history_id") or 0) >= int(current.get("history_id") or 0):
            latest[code] = normalized
    return latest
