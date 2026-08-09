from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from src.alpha_engine import AlphaDecisionEngine, AlphaFeatureAdapter

from .models import V6Signal


V6_ENGINE_VERSION = "v6.0-daily.1"
FORECAST_WEIGHTS = {
    "trend": 0.35,
    "momentum": 0.25,
    "relative_strength": 0.25,
    "market_regime": 0.15,
}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _find_mapping(root: Mapping[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    wanted = set(keys)
    queue: list[Mapping[str, Any]] = [root]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        for key, value in current.items():
            if key in wanted and isinstance(value, dict):
                return value
            if isinstance(value, dict):
                queue.append(value)
    return {}


def _find_value(root: Mapping[str, Any], keys: Iterable[str]) -> Any:
    wanted = set(keys)
    queue: list[Mapping[str, Any]] = [root]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        for key, value in current.items():
            if key in wanted and not isinstance(value, (dict, list, tuple, set)):
                return value
            if isinstance(value, dict):
                queue.append(value)
    return None


def _as_text_tuple(value: Any, *, limit: int = 5) -> Tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if not isinstance(value, (list, tuple)):
        return ()
    items = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return tuple(items)


def _weighted_forecast(features: Any) -> Tuple[Optional[float], float]:
    numerator = 0.0
    observed_weight = 0.0
    total_weight = sum(FORECAST_WEIGHTS.values())
    for name, weight in FORECAST_WEIGHTS.items():
        value = _finite(getattr(features, name, None))
        if value is None:
            continue
        value = max(0.0, min(100.0, value))
        numerator += value * weight
        observed_weight += weight
    if observed_weight <= 0:
        return None, 0.0
    return round(numerator / observed_weight, 2), round(observed_weight / total_weight, 4)


def _direction(score: Optional[float], coverage: float) -> str:
    if score is None or coverage < 0.50:
        return "neutral"
    if score >= 60.0:
        return "bullish"
    if score <= 40.0:
        return "bearish"
    return "neutral"


def _llm_health(raw_result: Dict[str, Any], primary_model: Optional[str]) -> Tuple[Optional[str], str]:
    if not raw_result:
        return None, "missing"
    success = raw_result.get("success")
    model = _find_value(raw_result, ("model_used",))
    model_text = str(model or "").strip() or None
    if success is False:
        return model_text, "degraded"
    if model_text and primary_model and model_text.strip().lower() != primary_model.strip().lower():
        return model_text, "fallback"
    if model_text:
        return model_text, "healthy"
    return None, "unknown"


def _market_breadth(context: Dict[str, Any]) -> Optional[str]:
    regime = _find_mapping(context, ("market_regime",))
    breadth = regime.get("market_breadth")
    if isinstance(breadth, dict):
        value = str(breadth.get("breadth") or "").strip().lower()
        return value or None
    value = str(regime.get("breadth") or "").strip().lower()
    return value or None


def _qualitative_intelligence(raw_result: Dict[str, Any]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    dashboard = _find_mapping(raw_result, ("dashboard",))
    intelligence = dashboard.get("intelligence") if isinstance(dashboard, dict) else None
    if not isinstance(intelligence, dict):
        intelligence = _find_mapping(raw_result, ("intelligence",))
    catalysts = _as_text_tuple(intelligence.get("positive_catalysts") if intelligence else None)
    risks = _as_text_tuple(intelligence.get("risk_alerts") if intelligence else None)
    return catalysts, risks


class V6DailyEngine:
    """Deterministic V6 daily forecast layer.

    LLM output is used only for qualitative catalyst/risk display and health
    telemetry. Direction, scores, evidence coverage and trade plan come only from
    structured market evidence and deterministic rules.
    """

    version = V6_ENGINE_VERSION

    def __init__(self) -> None:
        self.alpha = AlphaDecisionEngine()

    def from_analysis_record(
        self,
        record: Mapping[str, Any],
        *,
        primary_model: Optional[str] = None,
    ) -> Optional[V6Signal]:
        history_id = int(record.get("id") or 0)
        code = str(record.get("code") or "").strip().upper()
        if history_id <= 0 or not code:
            return None

        context = _parse_object(record.get("context_snapshot"))
        adapted = AlphaFeatureAdapter.from_snapshot(context)
        if adapted.current_price is None or adapted.current_price <= 0:
            return None

        decision = self.alpha.evaluate(
            code,
            adapted.features,
            current_price=adapted.current_price,
            support=adapted.support,
            resistance=adapted.resistance,
            atr=adapted.atr,
        )
        forecast_score, forecast_coverage = _weighted_forecast(adapted.features)
        direction = _direction(forecast_score, forecast_coverage)

        raw_result = _parse_object(record.get("raw_result"))
        model_used, llm_health = _llm_health(raw_result, primary_model)
        catalysts, risks = _qualitative_intelligence(raw_result)

        limitations = list(decision.limitations)
        if forecast_coverage < 0.50:
            limitations.append("directional forecast evidence coverage below 50%")
        if adapted.features.fundamental_quality is None:
            limitations.append("deterministic fundamental quality unavailable")
        if adapted.features.catalyst is None:
            limitations.append("catalyst remains qualitative-only")

        return V6Signal(
            analysis_history_id=history_id,
            query_id=record.get("query_id"),
            code=code,
            analysis_created_at=str(record.get("created_at") or ""),
            baseline_price=float(adapted.current_price),
            direction=direction,
            forecast_score=forecast_score,
            decision=decision.decision,
            quality_score=decision.quality_score,
            opportunity_score=decision.opportunity_score,
            risk_score=decision.risk_score,
            evidence_coverage=float(decision.evidence_coverage),
            market_regime=adapted.market_regime,
            market_breadth=_market_breadth(context),
            model_used=model_used,
            llm_health=llm_health,
            features=asdict(adapted.features),
            trade_plan=asdict(decision.trade_plan),
            catalysts=catalysts,
            risks=risks,
            limitations=tuple(dict.fromkeys(limitations)),
            diagnostics={
                "engine_version": self.version,
                "feature_adapter_version": AlphaFeatureAdapter.version,
                "forecast_component_coverage": forecast_coverage,
                "adapter": adapted.diagnostics,
                "llm_numeric_influence": "none",
            },
        )
