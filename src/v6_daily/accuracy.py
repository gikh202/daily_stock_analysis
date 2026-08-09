from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from src.alpha_engine.models import AlphaFeatures


HORIZONS: Tuple[int, ...] = (5, 10, 20)

# Distinct horizons intentionally use different evidence mixes.  The 5D model
# favours short-term confirmation; the 20D model gives more weight to durable
# trend/quality.  Missing evidence is re-normalised and lowers coverage.
STOCK_HORIZON_WEIGHTS: Dict[int, Dict[str, float]] = {
    5: {
        "trend": 0.20,
        "momentum": 0.30,
        "relative_strength": 0.20,
        "sector_relative_strength": 0.05,
        "volume_confirmation": 0.20,
        "market_regime": 0.05,
    },
    10: {
        "trend": 0.30,
        "momentum": 0.25,
        "relative_strength": 0.20,
        "sector_relative_strength": 0.05,
        "volume_confirmation": 0.05,
        "market_regime": 0.15,
    },
    20: {
        "trend": 0.25,
        "momentum": 0.10,
        "relative_strength": 0.15,
        "sector_relative_strength": 0.10,
        "fundamental_quality": 0.20,
        "catalyst": 0.05,
        "market_regime": 0.15,
    },
}

ETF_HORIZON_WEIGHTS: Dict[int, Dict[str, float]] = {
    5: {
        "trend": 0.20,
        "momentum": 0.30,
        "relative_strength": 0.25,
        "volume_confirmation": 0.15,
        "market_regime": 0.10,
    },
    10: {
        "trend": 0.30,
        "momentum": 0.20,
        "relative_strength": 0.25,
        "volume_confirmation": 0.10,
        "market_regime": 0.15,
    },
    20: {
        "trend": 0.35,
        "momentum": 0.10,
        "relative_strength": 0.30,
        "market_regime": 0.25,
    },
}

# Conservative neutral bands reduce false precision.  They can later be
# calibrated by walk-forward replay without changing the report contract.
HORIZON_BULLISH_THRESHOLD = {5: 60.0, 10: 60.0, 20: 62.0}
HORIZON_BEARISH_THRESHOLD = {5: 40.0, 10: 40.0, 20: 38.0}

KNOWN_ETFS = {
    "SPY", "VOO", "IVV", "VTI", "QQQ", "QQQM", "DIA", "IWM", "VT",
    "XLK", "XLC", "XLY", "XLP", "XLF", "XLV", "XLI", "XLE", "XLU",
    "XLRE", "XLB", "SMH", "SOXX", "ARKK", "SCHD", "JEPI", "JEPQ",
}

SECTOR_BENCHMARKS = {
    "technology": "XLK",
    "communication services": "XLC",
    "consumer discretionary": "XLY",
    "consumer staples": "XLP",
    "financial services": "XLF",
    "financial": "XLF",
    "healthcare": "XLV",
    "industrials": "XLI",
    "energy": "XLE",
    "utilities": "XLU",
    "real estate": "XLRE",
    "basic materials": "XLB",
    "materials": "XLB",
    "semiconductors": "SMH",
}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _find_mapping(root: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    wanted = {str(name) for name in names}
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


def _find_value(root: Mapping[str, Any], names: Iterable[str]) -> Any:
    wanted = {str(name) for name in names}
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


def classify_instrument(code: str, context: Mapping[str, Any]) -> str:
    symbol = str(code or "").strip().upper()
    explicit = str(
        _find_value(
            context,
            ("instrument_type", "quote_type", "security_type", "asset_type"),
        )
        or ""
    ).strip().upper()
    if explicit in {"ETF", "FUND", "MUTUALFUND", "INDEXFUND"}:
        return "ETF"
    if symbol in KNOWN_ETFS:
        return "ETF"
    return "STOCK"


def sector_benchmark(context: Mapping[str, Any]) -> Optional[str]:
    explicit = str(_find_value(context, ("sector_benchmark", "sector_etf")) or "").strip().upper()
    if explicit:
        return explicit
    sector = str(_find_value(context, ("sector",)) or "").strip().lower()
    return SECTOR_BENCHMARKS.get(sector)


def extract_sector_relative_strength(context: Mapping[str, Any]) -> Optional[float]:
    prediction = _find_mapping(context, ("prediction_context",))
    horizons = prediction.get("horizons") if isinstance(prediction, dict) else None
    if not isinstance(horizons, dict):
        return None
    scores: list[float] = []
    for horizon, scale in (("20d", 6.0), ("60d", 12.0)):
        block = horizons.get(horizon)
        if not isinstance(block, dict):
            continue
        value = _finite(block.get("excess_vs_sector_pct"))
        if value is None:
            continue
        scores.append(_clamp(50.0 + 50.0 * math.tanh(value / scale)))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def _evidence_age_hours(item: Mapping[str, Any]) -> Optional[float]:
    value = _finite(item.get("age_hours"))
    if value is not None:
        return max(0.0, value)
    timestamp = str(item.get("published_at") or item.get("filing_date") or "").strip()
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0)
    except ValueError:
        return None


def deterministic_catalyst_score(raw_result: Mapping[str, Any]) -> Tuple[Optional[float], Dict[str, Any]]:
    """Score only structured, source-backed events; never score free-form LLM prose."""
    dashboard = _find_mapping(raw_result, ("dashboard",))
    intelligence = dashboard.get("intelligence") if isinstance(dashboard, dict) else None
    if not isinstance(intelligence, dict):
        intelligence = _find_mapping(raw_result, ("intelligence",))

    candidates: Sequence[Any] = ()
    if isinstance(intelligence, dict):
        for key in ("evidence", "structured_evidence", "event_evidence", "catalyst_evidence"):
            value = intelligence.get(key)
            if isinstance(value, list):
                candidates = value
                break

    signed: list[float] = []
    accepted: list[Dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        direction_text = str(raw.get("direction") or raw.get("sentiment") or "").strip().lower()
        if direction_text in {"positive", "bullish", "利好", "正面"}:
            sign = 1.0
        elif direction_text in {"negative", "bearish", "利空", "负面"}:
            sign = -1.0
        else:
            continue

        source = str(raw.get("source_type") or raw.get("source") or "").strip().lower()
        source_weight = 1.0 if any(token in source for token in ("sec", "company", "ir", "official")) else 0.80
        reliability = _finite(raw.get("reliability") or raw.get("source_reliability"))
        if reliability is not None:
            source_weight *= _clamp(reliability, 0.0, 1.0)

        importance_text = str(raw.get("importance") or raw.get("materiality") or "medium").strip().lower()
        importance = {"high": 1.0, "material": 1.0, "medium": 0.70, "low": 0.40}.get(importance_text, 0.60)
        age = _evidence_age_hours(raw)
        freshness = 1.0 if age is None else max(0.25, math.exp(-age / (24.0 * 14.0)))
        contribution = sign * 32.0 * source_weight * importance * freshness
        signed.append(contribution)
        accepted.append({
            "direction": "positive" if sign > 0 else "negative",
            "source": source or "unknown",
            "importance": importance_text,
            "age_hours": None if age is None else round(age, 1),
            "contribution": round(contribution, 2),
        })

    if not signed:
        return None, {"eligible_events": 0, "rule": "structured-source-backed-only"}
    score = _clamp(50.0 + sum(signed))
    return round(score, 2), {
        "eligible_events": len(signed),
        "accepted": accepted[:8],
        "rule": "structured-source-backed-only",
    }


def external_fundamental_quality(external_context: Mapping[str, Any], code: str) -> Optional[float]:
    sec = external_context.get("sec") if isinstance(external_context, dict) else None
    item = sec.get(str(code).upper()) if isinstance(sec, dict) else None
    fundamentals = item.get("fundamentals") if isinstance(item, dict) else None
    return _finite(fundamentals.get("quality_score")) if isinstance(fundamentals, dict) else None


def external_macro_risk(external_context: Mapping[str, Any]) -> Optional[float]:
    fred = external_context.get("fred") if isinstance(external_context, dict) else None
    derived = fred.get("derived") if isinstance(fred, dict) else None
    return _finite(derived.get("macro_risk_score")) if isinstance(derived, dict) else None


def enrich_features(
    features: AlphaFeatures,
    *,
    context: Mapping[str, Any],
    raw_result: Mapping[str, Any],
    external_context: Optional[Mapping[str, Any]],
    code: str,
    current_price: Optional[float],
    support: Optional[float],
    atr: Optional[float],
) -> Tuple[AlphaFeatures, Dict[str, Any]]:
    sector_rs = extract_sector_relative_strength(context)
    catalyst, catalyst_diag = deterministic_catalyst_score(raw_result)
    fundamental = external_fundamental_quality(external_context or {}, code)
    macro_risk = external_macro_risk(external_context or {})

    gap_risk = _finite(_find_value(context, ("gap_risk_score", "gap_risk")))
    trend_breakdown_risk: Optional[float] = None
    price = _finite(current_price)
    support_f = _finite(support)
    atr_f = _finite(atr)
    if price is not None and support_f is not None and atr_f is not None and atr_f > 0:
        distance_atr = (price - support_f) / atr_f
        if distance_atr <= 0:
            trend_breakdown_risk = 92.0
        elif distance_atr < 0.5:
            trend_breakdown_risk = 78.0
        elif distance_atr < 1.0:
            trend_breakdown_risk = 62.0
        elif distance_atr < 2.0:
            trend_breakdown_risk = 42.0
        else:
            trend_breakdown_risk = 24.0

    updated = replace(
        features,
        sector_relative_strength=sector_rs if sector_rs is not None else features.sector_relative_strength,
        catalyst=catalyst if catalyst is not None else features.catalyst,
        fundamental_quality=(
            features.fundamental_quality
            if features.fundamental_quality is not None
            else fundamental
        ),
        gap_risk=gap_risk if gap_risk is not None else features.gap_risk,
        trend_breakdown_risk=(
            trend_breakdown_risk
            if trend_breakdown_risk is not None
            else features.trend_breakdown_risk
        ),
        macro_risk=macro_risk if macro_risk is not None else features.macro_risk,
    )
    return updated, {
        "sector_benchmark": sector_benchmark(context),
        "sector_relative_strength": sector_rs,
        "external_fundamental_quality": fundamental,
        "macro_risk": macro_risk,
        "gap_risk": gap_risk,
        "trend_breakdown_risk": trend_breakdown_risk,
        "catalyst": catalyst_diag,
    }


def _weighted_score(features: AlphaFeatures, weights: Mapping[str, float]) -> Tuple[Optional[float], float]:
    numerator = 0.0
    observed = 0.0
    total = sum(max(0.0, float(weight)) for weight in weights.values())
    for name, weight in weights.items():
        value = _finite(getattr(features, name, None))
        if value is None or weight <= 0:
            continue
        numerator += _clamp(value) * float(weight)
        observed += float(weight)
    if total <= 0 or observed <= 0:
        return None, 0.0
    return round(numerator / observed, 2), round(observed / total, 4)


def _direction(score: Optional[float], coverage: float, horizon: int) -> str:
    if score is None or coverage < 0.50:
        return "neutral"
    if score >= HORIZON_BULLISH_THRESHOLD[horizon]:
        return "bullish"
    if score <= HORIZON_BEARISH_THRESHOLD[horizon]:
        return "bearish"
    return "neutral"


def build_horizon_forecasts(features: AlphaFeatures, *, instrument_type: str) -> Dict[str, Dict[str, Any]]:
    profile = ETF_HORIZON_WEIGHTS if str(instrument_type).upper() == "ETF" else STOCK_HORIZON_WEIGHTS
    result: Dict[str, Dict[str, Any]] = {}
    for horizon in HORIZONS:
        score, coverage = _weighted_score(features, profile[horizon])
        result[f"{horizon}d"] = {
            "horizon_days": horizon,
            "score": score,
            "direction": _direction(score, coverage, horizon),
            "evidence_coverage": coverage,
            "weights": dict(profile[horizon]),
        }
    return result


def primary_forecast(horizons: Mapping[str, Mapping[str, Any]], primary_horizon: int = 10) -> Tuple[Optional[float], str, float]:
    block = horizons.get(f"{int(primary_horizon)}d") or {}
    score = _finite(block.get("score"))
    direction = str(block.get("direction") or "neutral")
    coverage = _finite(block.get("evidence_coverage")) or 0.0
    return score, direction, coverage
