from __future__ import annotations

import math
from typing import Dict, Iterable, Optional, Tuple

from .models import AlphaDecision, AlphaFeatures, TradePlan


def _finite(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _weighted_available(values: Dict[str, Optional[float]], weights: Dict[str, float]) -> Tuple[Optional[float], float]:
    """Score only observed features; return score and coverage of configured weight."""
    numerator = 0.0
    observed_weight = 0.0
    total_weight = sum(max(0.0, w) for w in weights.values())
    for name, weight in weights.items():
        value = _finite(values.get(name))
        if value is None or weight <= 0:
            continue
        numerator += _clamp(value) * weight
        observed_weight += weight
    if observed_weight <= 0 or total_weight <= 0:
        return None, 0.0
    return round(numerator / observed_weight, 1), round(observed_weight / total_weight, 4)


class AlphaDecisionEngine:
    """Deterministic, explainable decision layer.

    Design rules:
    - missing evidence lowers confidence instead of becoming a fake 50;
    - risk can veto sizing/action but never rewrites the upstream forecast;
    - position sizing is capped; this module never places orders;
    - LLM prose is not an input to the numeric score.
    """

    QUALITY_WEIGHTS = {
        "fundamental_quality": 0.55,
        "trend": 0.20,
        "relative_strength": 0.15,
        "data_quality": 0.10,
    }
    OPPORTUNITY_WEIGHTS = {
        "trend": 0.25,
        "momentum": 0.15,
        "relative_strength": 0.20,
        "volume_confirmation": 0.15,
        "catalyst": 0.10,
        "market_regime": 0.15,
    }
    RISK_WEIGHTS = {
        "volatility_risk": 0.55,
        "event_risk": 0.30,
        "data_risk": 0.15,
    }

    def evaluate(
        self,
        symbol: str,
        features: AlphaFeatures,
        *,
        current_price: Optional[float] = None,
        support: Optional[float] = None,
        resistance: Optional[float] = None,
        atr: Optional[float] = None,
    ) -> AlphaDecision:
        raw = features.__dict__.copy()
        data_quality = _finite(features.data_quality)
        raw["data_risk"] = None if data_quality is None else 100.0 - _clamp(data_quality)

        quality, quality_coverage = _weighted_available(raw, self.QUALITY_WEIGHTS)
        opportunity, opportunity_coverage = _weighted_available(raw, self.OPPORTUNITY_WEIGHTS)
        risk, risk_coverage = _weighted_available(raw, self.RISK_WEIGHTS)

        coverage = 0.45 * opportunity_coverage + 0.30 * quality_coverage + 0.25 * risk_coverage
        confidence = round(_clamp(coverage * 100.0) / 100.0, 2)

        limitations = []
        if opportunity_coverage < 0.65:
            limitations.append("opportunity evidence coverage below 65%")
        if risk_coverage < 0.70:
            limitations.append("risk evidence coverage below 70%")
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
            limitations=tuple(limitations),
            diagnostics={
                "quality_coverage": quality_coverage,
                "opportunity_coverage": opportunity_coverage,
                "risk_coverage": risk_coverage,
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
        # Hard portfolio-independent cap. Portfolio engine may reduce this further.
        max_position = 0.15 if risk <= 35 else 0.10 if risk <= 55 else 0.05
        if decision == "WATCH":
            max_position = min(max_position, 0.05)

        volatility_buffer = atr_f if atr_f is not None and atr_f > 0 else price * 0.025
        stop = support_f - 0.35 * volatility_buffer if support_f and support_f < price else price - 1.5 * volatility_buffer
        stop = max(0.01, stop)
        risk_per_share = max(0.01, price - stop)

        first_target = resistance_f if resistance_f and resistance_f > price else price + 2.0 * risk_per_share
        second_target = max(first_target, price + 3.0 * risk_per_share)
        rr = (first_target - price) / risk_per_share

        # Do not advertise a low-R:R setup as actionable.
        if rr < 1.5:
            return TradePlan(
                action="WAIT",
                max_position_pct=0.0,
                risk_reward=round(rr, 2),
                invalidation=("first target offers <1.5R",),
            )

        entry_low = min(price, support_f if support_f and support_f > stop else price)
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
