from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Tuple

from .models import AlphaDecision, AlphaFeatures, TradePlan


def _finite(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _weighted_available(
    values: Mapping[str, Optional[float]],
    weights: Mapping[str, float],
) -> Tuple[Optional[float], float]:
    """Score only observed features; return score and configured-weight coverage."""
    numerator = 0.0
    observed_weight = 0.0
    total_weight = sum(max(0.0, float(w)) for w in weights.values())
    for name, weight in weights.items():
        value = _finite(values.get(name))
        if value is None or weight <= 0:
            continue
        numerator += _clamp(value) * float(weight)
        observed_weight += float(weight)
    if observed_weight <= 0 or total_weight <= 0:
        return None, 0.0
    return round(numerator / observed_weight, 1), round(observed_weight / total_weight, 4)


class AlphaDecisionEngine:
    """Deterministic, explainable decision layer.

    V6.1 separates stock and ETF evidence profiles, adds source-backed catalyst
    and risk-v2 inputs, and continues to treat missing evidence as missing.
    LLM prose is never a numeric input.
    """

    STOCK_QUALITY_WEIGHTS = {
        "fundamental_quality": 0.50,
        "trend": 0.18,
        "relative_strength": 0.12,
        "sector_relative_strength": 0.10,
        "data_quality": 0.10,
    }
    ETF_QUALITY_WEIGHTS = {
        "trend": 0.30,
        "relative_strength": 0.30,
        "market_regime": 0.25,
        "data_quality": 0.15,
    }
    STOCK_OPPORTUNITY_WEIGHTS = {
        "trend": 0.22,
        "momentum": 0.14,
        "relative_strength": 0.16,
        "sector_relative_strength": 0.10,
        "volume_confirmation": 0.12,
        "catalyst": 0.10,
        "market_regime": 0.16,
    }
    ETF_OPPORTUNITY_WEIGHTS = {
        "trend": 0.28,
        "momentum": 0.18,
        "relative_strength": 0.25,
        "volume_confirmation": 0.12,
        "market_regime": 0.17,
    }
    RISK_WEIGHTS = {
        "volatility_risk": 0.30,
        "event_risk": 0.18,
        "gap_risk": 0.12,
        "trend_breakdown_risk": 0.15,
        "macro_risk": 0.15,
        "data_risk": 0.10,
    }

    def _profiles(self, instrument_type: str) -> Tuple[Dict[str, float], Dict[str, float]]:
        if str(instrument_type or "").strip().upper() == "ETF":
            return self.ETF_QUALITY_WEIGHTS, self.ETF_OPPORTUNITY_WEIGHTS
        return self.STOCK_QUALITY_WEIGHTS, self.STOCK_OPPORTUNITY_WEIGHTS

    def evaluate(
        self,
        symbol: str,
        features: AlphaFeatures,
        *,
        current_price: Optional[float] = None,
        support: Optional[float] = None,
        resistance: Optional[float] = None,
        atr: Optional[float] = None,
        instrument_type: str = "STOCK",
    ) -> AlphaDecision:
        raw = features.__dict__.copy()
        data_quality = _finite(features.data_quality)
        raw["data_risk"] = None if data_quality is None else 100.0 - _clamp(data_quality)

        quality_weights, opportunity_weights = self._profiles(instrument_type)
        quality, quality_coverage = _weighted_available(raw, quality_weights)
        opportunity, opportunity_coverage = _weighted_available(raw, opportunity_weights)
        risk, risk_coverage = _weighted_available(raw, self.RISK_WEIGHTS)

        coverage = 0.45 * opportunity_coverage + 0.30 * quality_coverage + 0.25 * risk_coverage
        confidence = round(_clamp(coverage * 100.0) / 100.0, 2)

        limitations = []
        if opportunity_coverage < 0.65:
            limitations.append("opportunity evidence coverage below 65%")
        if risk_coverage < 0.60:
            limitations.append("risk evidence coverage below 60%")
        if data_quality is not None and data_quality < 60:
            limitations.append("data quality below 60")

        decision = "WAIT"
        if opportunity is not None and risk is not None and confidence >= 0.60:
            if risk >= 75:
                decision = "AVOID"
            elif opportunity >= 75 and risk <= 45:
                decision = "BUY_SETUP"
            elif opportunity >= 60 and risk <= 60:
                decision = "WATCH"
            elif opportunity < 40 and risk >= 55:
                decision = "AVOID"

        plan = self._build_trade_plan(
            decision,
            risk,
            current_price=current_price,
            support=support,
            resistance=resistance,
            atr=atr,
        )
        if plan.action != decision:
            limitations.append(f"trade-plan gate downgraded decision {decision}->{plan.action}")
            decision = plan.action

        reasons = []
        if opportunity is not None:
            reasons.append(f"opportunity={opportunity:.1f}")
        if quality is not None:
            reasons.append(f"quality={quality:.1f}")
        if risk is not None:
            reasons.append(f"risk={risk:.1f}")

        return AlphaDecision(
            symbol=str(symbol or "").strip().upper(),
            quality_score=quality,
            opportunity_score=opportunity,
            risk_score=risk,
            confidence=confidence,
            decision=decision,
            features=features,
            trade_plan=plan,
            reasons=tuple(reasons),
            limitations=tuple(dict.fromkeys(limitations)),
            diagnostics={
                "instrument_type": str(instrument_type or "STOCK").upper(),
                "quality_coverage": quality_coverage,
                "opportunity_coverage": opportunity_coverage,
                "risk_coverage": risk_coverage,
                "quality_weights": quality_weights,
                "opportunity_weights": opportunity_weights,
                "risk_weights": self.RISK_WEIGHTS,
            },
        )

    @staticmethod
    def _build_trade_plan(
        decision: str,
        risk_score: Optional[float],
        *,
        current_price: Optional[float],
        support: Optional[float],
        resistance: Optional[float],
        atr: Optional[float],
    ) -> TradePlan:
        price = _finite(current_price)
        support_f = _finite(support)
        resistance_f = _finite(resistance)
        atr_f = _finite(atr)

        if price is None or price <= 0 or decision not in {"BUY_SETUP", "WATCH"}:
            return TradePlan(action=decision, max_position_pct=0.0)

        risk = 50.0 if risk_score is None else _clamp(risk_score)
        max_position = 0.15 if risk <= 35 else 0.10 if risk <= 55 else 0.05
        if decision == "WATCH":
            max_position = min(max_position, 0.05)

        volatility_buffer = atr_f if atr_f is not None and atr_f > 0 else price * 0.025
        stop = (
            support_f - 0.35 * volatility_buffer
            if support_f is not None and 0 < support_f < price
            else price - 1.5 * volatility_buffer
        )
        stop = max(0.01, stop)
        risk_per_share = max(0.01, price - stop)

        first_target = (
            resistance_f
            if resistance_f is not None and resistance_f > price
            else price + 2.0 * risk_per_share
        )
        second_target = max(first_target, price + 3.0 * risk_per_share)
        rr = (first_target - price) / risk_per_share
        if rr < 1.5:
            return TradePlan(
                action="WAIT",
                max_position_pct=0.0,
                risk_reward=round(rr, 2),
                invalidation=("first target offers <1.5R",),
            )

        # ATR-based entry width avoids the former [price, price] pseudo-zone.
        zone_width = max(0.25 * volatility_buffer, price * 0.0025)
        technical_floor = stop + 0.55 * risk_per_share
        entry_low = max(technical_floor, price - zone_width)
        if support_f is not None and stop < support_f < price:
            entry_low = max(entry_low, min(price, support_f))
        entry_high = price

        return TradePlan(
            action=decision,
            entry_zone=(round(entry_low, 4), round(entry_high, 4)),
            stop_loss=round(stop, 4),
            targets=(round(first_target, 4), round(second_target, 4)),
            max_position_pct=max_position,
            risk_reward=round(rr, 2),
            invalidation=(f"close below {stop:.4f}",),
            confirmations=("price/volume confirmation required before entry",),
        )
