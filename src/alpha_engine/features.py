from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .models import AlphaFeatures


FEATURE_ADAPTER_VERSION = "v5.0-shadow.2"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
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


def _find_value(root: Mapping[str, Any], keys: Sequence[str]) -> Any:
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


def _first_number(mapping: Mapping[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = _finite(mapping.get(key))
        if value is not None:
            return value
    return None


def _return_score(value: Optional[float], *, scale: float) -> Optional[float]:
    if value is None:
        return None
    return round(
        _clamp(50.0 + 50.0 * math.tanh(value / max(scale, 0.1))),
        2,
    )


def _average(values: Iterable[Optional[float]]) -> Optional[float]:
    observed = [v for v in values if v is not None and math.isfinite(v)]
    if not observed:
        return None
    return round(sum(observed) / len(observed), 2)


def _market_regime_score(regime: Mapping[str, Any]) -> Optional[float]:
    label = str(regime.get("regime") or "").strip().lower()
    base = {"risk_on": 80.0, "neutral": 50.0, "risk_off": 20.0}.get(label)
    if base is None:
        return None
    breadth = regime.get("market_breadth")
    if isinstance(breadth, dict):
        breadth_label = str(breadth.get("breadth") or "").strip().lower()
        base += {"broad": 10.0, "neutral": 0.0, "narrow": -10.0}.get(
            breadth_label,
            0.0,
        )
    return round(_clamp(base), 2)


def _volatility_risk(realized_vol_pct: Optional[float]) -> Optional[float]:
    if realized_vol_pct is None or realized_vol_pct < 0:
        return None
    return round(
        _clamp(100.0 * realized_vol_pct / (realized_vol_pct + 28.0)),
        2,
    )


def _event_risk(days_until_event: Optional[float]) -> Optional[float]:
    if days_until_event is None:
        return None
    days = max(0.0, days_until_event)
    if days <= 2:
        return 92.0
    if days <= 7:
        return 75.0
    if days <= 14:
        return 50.0
    if days <= 30:
        return 30.0
    return 18.0


def _volume_confirmation(
    rvol: Optional[float],
    short_return_pct: Optional[float],
) -> Optional[float]:
    if rvol is None or rvol < 0:
        return None
    intensity = math.tanh((rvol - 1.0) / 0.6)
    if short_return_pct is None:
        return round(_clamp(50.0 + 12.0 * intensity), 2)
    direction = max(-1.0, min(1.0, short_return_pct / 3.0))
    return round(_clamp(50.0 + 35.0 * intensity * direction), 2)


def _extract_horizon(
    prediction_context: Mapping[str, Any],
    horizon: str,
) -> Dict[str, Any]:
    horizons = prediction_context.get("horizons")
    if not isinstance(horizons, dict):
        return {}
    value = horizons.get(horizon)
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class AdaptedAlphaInput:
    features: AlphaFeatures
    current_price: Optional[float]
    support: Optional[float]
    resistance: Optional[float]
    atr: Optional[float]
    market_regime: Optional[str]
    diagnostics: Dict[str, Any]


class AlphaFeatureAdapter:
    """Map persisted V4 structured artifacts into deterministic V5 features.

    The adapter consumes the actual V4 snapshot contracts (`trend_analysis`,
    `volume_price_features`, `prediction_context`, `market_regime`, etc.). It
    never scores LLM prose and never converts missing evidence into a fake 50.
    """

    version = FEATURE_ADAPTER_VERSION

    @classmethod
    def from_snapshot(cls, context_snapshot: Any) -> AdaptedAlphaInput:
        root = _as_dict(context_snapshot)
        trend = _find_mapping(
            root,
            ("trend_analysis", "trend_result", "technical_analysis"),
        )
        volume = _find_mapping(root, ("volume_price_features",))
        prediction = _find_mapping(root, ("prediction_context",))
        regime = _find_mapping(root, ("market_regime",))
        earnings = _find_mapping(
            root,
            ("earnings_event", "earnings_context", "earnings"),
        )
        fundamental = _find_mapping(
            root,
            ("fundamental_context", "fundamentals"),
        )
        market_structure = _find_mapping(
            root,
            ("market_structure_context", "market_structure"),
        )

        trend_score = _first_number(
            trend,
            ("signal_score", "trend_score", "trend_strength"),
        )
        if trend_score is not None:
            trend_score = _clamp(trend_score)

        h5 = _extract_horizon(prediction, "5d")
        h20 = _extract_horizon(prediction, "20d")
        h60 = _extract_horizon(prediction, "60d")
        ret5 = _first_number(h5, ("target_return_pct",))
        ret20 = _first_number(h20, ("target_return_pct",))
        ret60 = _first_number(h60, ("target_return_pct",))
        momentum = _average(
            (
                _return_score(ret20, scale=8.0),
                _return_score(ret60, scale=16.0),
            )
        )

        relative_strength = _average(
            _return_score(_first_number(block, (key,)), scale=scale)
            for block, scale in ((h20, 6.0), (h60, 12.0))
            for key in ("excess_vs_spy_pct", "excess_vs_qqq_pct")
        )

        # V4 production writes complete-daily-bar RVOL here. Prefer the 20-day
        # denominator and fall back to 5-day only when the longer sample is not
        # available. Legacy aliases remain accepted for older snapshots.
        rvol = _first_number(volume, ("rvol20", "rvol5"))
        if rvol is None:
            rvol = _finite(
                _find_value(
                    root,
                    ("rvol20", "rvol5", "rvol", "relative_volume", "volume_ratio_5d"),
                )
            )
        volume_confirmation = _volume_confirmation(rvol, ret5)

        fundamental_quality = _first_number(
            fundamental,
            ("quality_score", "fundamental_score", "financial_quality_score"),
        )
        if fundamental_quality is not None:
            fundamental_quality = _clamp(fundamental_quality)

        market_regime = _market_regime_score(regime)
        realized_vol = _first_number(
            prediction,
            ("realized_vol_20d_pct", "realized_volatility_20d_pct"),
        )
        volatility_risk = _volatility_risk(realized_vol)

        days_until_earnings = _first_number(
            earnings,
            ("days_to_earnings", "days_until_earnings", "days_until_event"),
        )
        event_risk = _event_risk(days_until_earnings)

        # Deliberately unavailable until there is a deterministic signed,
        # Evidence-ID-backed catalyst classifier. News count is not sentiment.
        catalyst = None

        observed_groups = {
            "trend": trend_score is not None,
            "momentum": momentum is not None,
            "relative_strength": relative_strength is not None,
            "volume": volume_confirmation is not None,
            "fundamental": fundamental_quality is not None,
            "market_regime": market_regime is not None,
            "volatility": volatility_risk is not None,
            "event_risk": event_risk is not None,
        }
        data_quality = round(
            100.0
            * sum(1 for observed in observed_groups.values() if observed)
            / len(observed_groups),
            2,
        )

        realtime = _find_mapping(root, ("realtime", "realtime_quote"))
        today = _find_mapping(root, ("today",))
        current_price = _first_number(
            realtime,
            ("price", "close"),
        )
        if current_price is None:
            current_price = _first_number(today, ("close", "price"))
        if current_price is None:
            current_price = _first_number(trend, ("current_price", "close", "price"))

        support = _first_number(market_structure, ("support_level", "support"))
        resistance = _first_number(
            market_structure,
            ("resistance_level", "resistance"),
        )

        raw_support = trend.get("support_levels") if isinstance(trend, dict) else None
        if support is None and isinstance(raw_support, (list, tuple)):
            values = [v for v in (_finite(item) for item in raw_support) if v is not None and v > 0]
            if values:
                below = [v for v in values if current_price is not None and v < current_price]
                support = max(below) if below else min(values)

        raw_resistance = (
            trend.get("resistance_levels") if isinstance(trend, dict) else None
        )
        if resistance is None and isinstance(raw_resistance, (list, tuple)):
            values = [v for v in (_finite(item) for item in raw_resistance) if v is not None and v > 0]
            if values:
                above = [v for v in values if current_price is not None and v > current_price]
                resistance = min(above) if above else max(values)

        atr = _finite(_find_value(root, ("atr", "atr_14", "atr14")))
        regime_label = str(regime.get("regime") or "").strip().lower() or None

        return AdaptedAlphaInput(
            features=AlphaFeatures(
                trend=trend_score,
                momentum=momentum,
                relative_strength=relative_strength,
                volume_confirmation=volume_confirmation,
                fundamental_quality=fundamental_quality,
                catalyst=catalyst,
                market_regime=market_regime,
                volatility_risk=volatility_risk,
                event_risk=event_risk,
                data_quality=data_quality,
            ),
            current_price=current_price,
            support=support,
            resistance=resistance,
            atr=atr,
            market_regime=regime_label,
            diagnostics={
                "adapter_version": cls.version,
                "observed_groups": observed_groups,
                "rvol": rvol,
                "return_5d_pct": ret5,
                "return_20d_pct": ret20,
                "return_60d_pct": ret60,
                "realized_vol_20d_pct": realized_vol,
                "days_until_earnings": days_until_earnings,
            },
        )
