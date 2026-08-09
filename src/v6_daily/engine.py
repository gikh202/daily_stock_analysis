from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from src.alpha_engine import AlphaDecisionEngine, AlphaFeatureAdapter

from .accuracy import (
    build_horizon_forecasts,
    classify_instrument,
    enrich_features,
    primary_forecast,
)
from .models import V6Signal


V6_ENGINE_VERSION = "v6.1-accuracy.1"


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
                return dict(value)
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


def _effective_trade_date(context: Mapping[str, Any], created_at: Any) -> Optional[str]:
    value = _find_value(
        context,
        (
            "effective_daily_bar_date",
            "effective_trade_date",
            "last_complete_trade_date",
        ),
    )
    text = str(value or "").strip()
    if len(text) >= 10:
        return text[:10]
    created = str(created_at or "").strip()
    return created[:10] if len(created) >= 10 else None


class V6DailyEngine:
    """Deterministic V6.1 multi-horizon forecast layer.

    Numeric decisions are produced from structured evidence only. LLM prose is
    retained for explanation, but cannot directly change a score. Structured,
    source-backed catalyst evidence may be scored by deterministic rules.
    """

    version = V6_ENGINE_VERSION

    def __init__(self) -> None:
        self.alpha = AlphaDecisionEngine()

    def from_analysis_record(
        self,
        record: Mapping[str, Any],
        *,
        primary_model: Optional[str] = None,
        external_context: Optional[Mapping[str, Any]] = None,
    ) -> Optional[V6Signal]:
        history_id = int(record.get("id") or 0)
        code = str(record.get("code") or "").strip().upper()
        if history_id <= 0 or not code:
            return None

        context = _parse_object(record.get("context_snapshot"))
        raw_result = _parse_object(record.get("raw_result"))
        adapted = AlphaFeatureAdapter.from_snapshot(context)
        if adapted.current_price is None or adapted.current_price <= 0:
            return None

        instrument_type = classify_instrument(code, context)
        features, accuracy_diag = enrich_features(
            adapted.features,
            context=context,
            raw_result=raw_result,
            external_context=external_context,
            code=code,
            current_price=adapted.current_price,
            support=adapted.support,
            atr=adapted.atr,
        )

        horizons = build_horizon_forecasts(features, instrument_type=instrument_type)
        forecast_score, direction, forecast_coverage = primary_forecast(horizons, 10)

        decision = self.alpha.evaluate(
            code,
            features,
            current_price=adapted.current_price,
            support=adapted.support,
            resistance=adapted.resistance,
            atr=adapted.atr,
            instrument_type=instrument_type,
        )

        model_used, llm_health = _llm_health(raw_result, primary_model)
        catalysts, risks = _qualitative_intelligence(raw_result)

        limitations = list(decision.limitations)
        if forecast_coverage < 0.50:
            limitations.append("10d directional forecast evidence coverage below 50%")
        if instrument_type == "STOCK" and features.fundamental_quality is None:
            limitations.append("deterministic fundamental quality unavailable")
        if features.catalyst is None:
            limitations.append("source-backed deterministic catalyst unavailable")
        if features.macro_risk is None:
            limitations.append("macro risk snapshot unavailable")

        effective_date = _effective_trade_date(context, record.get("created_at"))
        context_features = {
            "instrument_type": instrument_type,
            "effective_trade_date": effective_date,
            "market_regime": adapted.market_regime,
            "market_breadth": _market_breadth(context),
            "accuracy": accuracy_diag,
        }

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
            features=asdict(features),
            trade_plan=asdict(decision.trade_plan),
            catalysts=catalysts,
            risks=risks,
            limitations=tuple(dict.fromkeys(limitations)),
            diagnostics={
                "engine_version": self.version,
                "feature_adapter_version": AlphaFeatureAdapter.version,
                "primary_horizon": "10d",
                "forecast_component_coverage": forecast_coverage,
                "adapter": adapted.diagnostics,
                "accuracy": accuracy_diag,
                "alpha": decision.diagnostics,
                "llm_numeric_influence": "none",
            },
            instrument_type=instrument_type,
            effective_trade_date=effective_date,
            horizon_forecasts=horizons,
            context_features=context_features,
        )
