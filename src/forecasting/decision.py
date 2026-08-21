from __future__ import annotations

import math
from typing import Any, Optional

from .models import ForecastBundle, ForecastDecision


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ForecastDecisionPolicy:
    """Convert calibrated distributions into a risk-bounded trade setup."""

    version = "v7.0-decision.1"

    def decide(self, bundle: ForecastBundle, *, risk_score: float | None, opportunity_score: float | None) -> ForecastDecision:
        h1, h5, h20 = bundle.horizons["1d"], bundle.horizons["5d"], bundle.horizons["20d"]
        risk = 50.0 if _finite(risk_score) is None else _clamp(float(risk_score), 0.0, 100.0)
        downside = max(0.01, abs(min(0.0, h5.p10_return_pct, h5.expected_mae_pct)))
        upside = max(0.0, h5.p50_return_pct, h5.expected_return_pct)
        reward_risk = upside / downside
        edge = h5.expected_alpha_vs_spy_pct
        confidence = min(h1.forecast_confidence, h5.forecast_confidence, h20.forecast_confidence)
        gates: list[str] = []
        if risk >= 75.0:
            return ForecastDecision("AVOID", "risk gate >=75 blocks new exposure regardless of directional forecast", confidence, edge, downside, 0.0, ("hard_risk_gate",))
        if h5.calibration_status == "prior_only":
            gates.append("probability_not_yet_calibrated")
        if confidence < 0.50:
            gates.append("forecast_confidence_below_50pct")
        if h5.expected_alpha_vs_spy_pct <= 0:
            gates.append("non_positive_expected_alpha")
        if reward_risk < 1.15:
            gates.append("insufficient_distribution_reward_risk")

        bearish = bool(h5.probability_up <= 0.42 and h5.expected_return_pct < 0.0 and h20.probability_up <= 0.47)
        constructive = bool(h5.probability_up >= 0.58 and h5.expected_return_pct > 0.35 and h5.expected_alpha_vs_spy_pct > 0.0 and h20.probability_up >= 0.52 and h20.expected_return_pct >= 0.0 and reward_risk >= 1.15 and confidence >= 0.50 and risk <= 60.0)
        watchable = bool(h5.probability_up >= 0.52 and h5.expected_return_pct > 0.0 and h20.probability_up >= 0.48 and risk <= 68.0)
        if bearish:
            decision = "AVOID"
            rationale = f"5D/20D calibrated probabilities are bearish ({h5.probability_up:.1%}/{h20.probability_up:.1%}) with negative expected return"
        elif gates:
            decision = "WAIT"
            rationale = "execution gates block new exposure: " + ", ".join(gates)
        elif constructive:
            decision = "BUY_SETUP"
            rationale = f"5D calibrated P(up)={h5.probability_up:.1%}, expected return {h5.expected_return_pct:+.2f}%, expected alpha {h5.expected_alpha_vs_spy_pct:+.2f}%, distribution R/R={reward_risk:.2f}"
        elif watchable:
            decision = "WATCH"
            rationale = f"forecast is constructive but edge is not strong enough for immediate setup: 5D P(up)={h5.probability_up:.1%}, return={h5.expected_return_pct:+.2f}%, alpha={h5.expected_alpha_vs_spy_pct:+.2f}%"
        else:
            decision = "WAIT"
            rationale = f"forecast edge is insufficient: 5D P(up)={h5.probability_up:.1%}, return={h5.expected_return_pct:+.2f}%, alpha={h5.expected_alpha_vs_spy_pct:+.2f}%"

        if decision in {"AVOID", "WAIT"}:
            max_position = 0.0
        else:
            risk_cap = 0.15 if risk <= 35 else 0.10 if risk <= 55 else 0.05
            confidence_cap = 0.05 if confidence < 0.60 else 0.10 if confidence < 0.75 else 0.15
            edge_cap = 0.05 if h5.probability_up < 0.62 else 0.10 if h5.probability_up < 0.70 else 0.15
            max_position = min(risk_cap, confidence_cap, edge_cap)
            if decision == "WATCH":
                max_position = min(max_position, 0.05)
        return ForecastDecision(decision, rationale, round(confidence, 4), round(edge, 4), round(downside, 4), round(max_position, 4), tuple(gates))

    def build_trade_plan(self, forecast_decision: ForecastDecision, *, risk_score: float | None, current_price: float | None, support: float | None, resistance: float | None, atr: float | None) -> dict[str, Any]:
        decision = forecast_decision.decision
        price, support_f, resistance_f, atr_f = _finite(current_price), _finite(support), _finite(resistance), _finite(atr)
        if price is None or price <= 0 or decision not in {"BUY_SETUP", "WATCH"}:
            return {"action": decision, "entry_zone": None, "stop_loss": None, "targets": (), "max_position_pct": 0.0, "risk_reward": None, "invalidation": (), "confirmations": ()}
        volatility_buffer = atr_f if atr_f is not None and atr_f > 0 else price * 0.025
        stop = support_f - 0.35 * volatility_buffer if support_f is not None and 0 < support_f < price else price - 1.5 * volatility_buffer
        stop = max(0.01, stop)
        risk_per_share = max(0.01, price - stop)
        first_target = resistance_f if resistance_f is not None and resistance_f > price else price + 2.0 * risk_per_share
        second_target = max(first_target, price + 3.0 * risk_per_share)
        rr = (first_target - price) / risk_per_share
        if rr < 1.5:
            return {"action": "WAIT", "entry_zone": None, "stop_loss": round(stop,4), "targets": (round(first_target,4), round(second_target,4)), "max_position_pct": 0.0, "risk_reward": round(rr,2), "invalidation": ("first target offers <1.5R",), "confirmations": ()}
        zone_width = max(0.25 * volatility_buffer, price * 0.0025)
        technical_floor = stop + 0.55 * risk_per_share
        entry_low = max(technical_floor, price - zone_width)
        if support_f is not None and stop < support_f < price:
            entry_low = max(entry_low, min(price, support_f))
        return {"action": decision, "entry_zone": (round(entry_low,4), round(price,4)), "stop_loss": round(stop,4), "targets": (round(first_target,4), round(second_target,4)), "max_position_pct": forecast_decision.max_position_fraction, "risk_reward": round(rr,2), "invalidation": (f"close below {stop:.4f}",), "confirmations": ("current price remains inside the risk-bounded entry zone", "intraday timing model does not predict a materially better near-term entry")}
