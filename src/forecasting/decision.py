from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from .models import ForecastBundle, ForecastDecision, ForecastHorizon


MIN_MATURE_SAMPLES = 50
MIN_DIRECTION_HIT_RATE = 0.45
FULL_DIRECTION_HIT_RATE = 0.52


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def forecast_reliability_weight(horizon: ForecastHorizon) -> float:
    """Return a conservative trading weight for one forecast horizon.

    V7.4 separates research tendency from tradable direction:
    - fewer than 50 mature samples: zero trading weight;
    - direction hit rate <45%: zero trading weight;
    - 45%-52%: observation-only low weight;
    - >=52%: sample-scaled trading weight.
    """
    samples = max(0, int(horizon.calibration_samples or 0))
    status = str(horizon.calibration_status or "prior_only").strip().lower()
    hit_rate = horizon.historical_direction_hit_rate
    if status != "mature" or samples < MIN_MATURE_SAMPLES:
        return 0.0
    if hit_rate is None or hit_rate < MIN_DIRECTION_HIT_RATE:
        return 0.0
    sample_weight = min(1.0, samples / 100.0)
    if hit_rate < FULL_DIRECTION_HIT_RATE:
        return round(0.10 * sample_weight, 4)
    return round(sample_weight, 4)


def reliability_aware_direction(horizon: ForecastHorizon) -> str:
    """Expose strong bullish/bearish labels only when historical reliability permits."""
    if forecast_reliability_weight(horizon) <= 0.0:
        return "neutral"
    return str(horizon.direction or "neutral")


def _feature_value(features: Any, name: str) -> Optional[float]:
    if isinstance(features, Mapping):
        return _finite(features.get(name))
    return _finite(getattr(features, name, None))


def momentum_continuation_score(features: Any, regime: str | None) -> float:
    """Detect risk-on trend continuation without treating overbought as automatic reversal.

    This is an execution-context signal, not a calibrated probability. It requires
    simultaneous trend, momentum, relative-strength and non-weak volume confirmation.
    """
    if str(regime or "").strip().lower() != "risk_on" or features is None:
        return 0.0
    trend = _feature_value(features, "trend")
    momentum = _feature_value(features, "momentum")
    relative = _feature_value(features, "relative_strength")
    volume = _feature_value(features, "volume_confirmation")
    market = _feature_value(features, "market_regime")
    if None in {trend, momentum, relative, volume}:
        return 0.0
    assert trend is not None and momentum is not None and relative is not None and volume is not None
    if trend < 55.0 or momentum < 70.0 or relative < 65.0 or volume < 40.0:
        return 0.0
    if market is not None and market < 60.0:
        return 0.0
    strength = (
        0.25 * _clamp((trend - 55.0) / 30.0, 0.0, 1.0)
        + 0.35 * _clamp((momentum - 70.0) / 30.0, 0.0, 1.0)
        + 0.25 * _clamp((relative - 65.0) / 30.0, 0.0, 1.0)
        + 0.15 * _clamp((volume - 40.0) / 40.0, 0.0, 1.0)
    )
    return round(_clamp(0.60 + 0.40 * strength, 0.0, 1.0), 4)


def _effective_probability(horizon: ForecastHorizon, weight: float) -> float:
    """Shrink unreliable directional probabilities toward neutral for execution only."""
    p = _clamp(float(horizon.probability_up), 0.02, 0.98)
    w = _clamp(float(weight), 0.0, 1.0)
    return 0.50 + (p - 0.50) * w


class ForecastDecisionPolicy:
    """Convert calibrated distributions into a risk-bounded trade setup."""

    version = "v7.4-decision-reliability-continuation.1"

    def decide(
        self,
        bundle: ForecastBundle,
        *,
        risk_score: float | None,
        opportunity_score: float | None,
        features: Any | None = None,
    ) -> ForecastDecision:
        h1, h5, h20 = (
            bundle.horizons["1d"],
            bundle.horizons["5d"],
            bundle.horizons["20d"],
        )
        risk = (
            50.0
            if _finite(risk_score) is None
            else _clamp(float(risk_score), 0.0, 100.0)
        )
        downside = max(0.01, abs(min(0.0, h5.p10_return_pct, h5.expected_mae_pct)))
        upside = max(0.0, h5.p50_return_pct, h5.expected_return_pct)
        reward_risk = upside / downside
        edge = h5.expected_alpha_vs_spy_pct
        confidence = min(
            h1.forecast_confidence,
            h5.forecast_confidence,
            h20.forecast_confidence,
        )
        w1 = forecast_reliability_weight(h1)
        w5 = forecast_reliability_weight(h5)
        w20 = forecast_reliability_weight(h20)
        p1 = _effective_probability(h1, w1)
        p5 = _effective_probability(h5, w5)
        p20 = _effective_probability(h20, w20)
        continuation = momentum_continuation_score(features, bundle.regime)
        strong_continuation = continuation >= 0.65

        gates: list[str] = []
        if risk >= 75.0:
            return ForecastDecision(
                "AVOID",
                "risk gate >=75 blocks new exposure regardless of directional forecast",
                confidence,
                edge,
                downside,
                0.0,
                ("hard_risk_gate",),
            )
        if str(h5.calibration_status or "").strip().lower() == "prior_only":
            gates.append("probability_not_yet_calibrated")
        if w5 <= 0.0:
            gates.append("5d_reliability_below_trading_floor")
        if confidence < 0.50:
            gates.append("forecast_confidence_below_50pct")
        if h5.expected_alpha_vs_spy_pct <= 0:
            gates.append("non_positive_expected_alpha")
        if reward_risk < 1.15:
            gates.append("insufficient_distribution_reward_risk")

        bearish = bool(
            w5 >= 0.25
            and p5 <= 0.42
            and h5.expected_return_pct < 0.0
            and (w20 <= 0.0 or p20 <= 0.47)
        )
        constructive = bool(
            w5 >= 0.25
            and p5 >= 0.58
            and h5.expected_return_pct > 0.35
            and h5.expected_alpha_vs_spy_pct > 0.0
            and (w20 <= 0.0 or p20 >= 0.52)
            and h20.expected_return_pct >= 0.0
            and reward_risk >= 1.15
            and confidence >= 0.50
            and risk <= 60.0
        )
        watchable = bool(
            w5 > 0.0
            and p5 >= 0.52
            and h5.expected_return_pct > 0.0
            and (w20 <= 0.0 or p20 >= 0.48)
            and risk <= 68.0
        )

        if bearish and strong_continuation:
            decision = "WAIT"
            rationale = (
                "reliable bearish forecast conflicts with a strong risk-on momentum "
                f"continuation signal ({continuation:.0%}); do not short-circuit into AVOID, "
                "wait for confirmation"
            )
        elif bearish:
            decision = "AVOID"
            rationale = (
                "reliability-weighted 5D/20D probabilities are bearish "
                f"({p5:.1%}/{p20:.1%}) with negative expected return"
            )
        elif gates:
            decision = "WAIT"
            continuation_note = (
                f"; strong momentum-continuation signal observed ({continuation:.0%})"
                if strong_continuation
                else ""
            )
            rationale = "execution gates block new exposure: " + ", ".join(gates) + continuation_note
        elif constructive:
            decision = "BUY_SETUP"
            rationale = (
                f"reliability-weighted 5D P(up)={p5:.1%}, expected return "
                f"{h5.expected_return_pct:+.2f}%, expected alpha "
                f"{h5.expected_alpha_vs_spy_pct:+.2f}%, distribution R/R={reward_risk:.2f}"
            )
        elif watchable or strong_continuation:
            decision = "WATCH"
            rationale = (
                "forecast/continuation evidence is constructive but not strong enough "
                f"for immediate setup: weighted 5D P(up)={p5:.1%}, "
                f"continuation={continuation:.0%}, return={h5.expected_return_pct:+.2f}%, "
                f"alpha={h5.expected_alpha_vs_spy_pct:+.2f}%"
            )
        else:
            decision = "WAIT"
            rationale = (
                "forecast edge is insufficient after reliability weighting: "
                f"1D={p1:.1%}, 5D={p5:.1%}, 20D={p20:.1%}, "
                f"5D reliability weight={w5:.0%}"
            )

        if decision in {"AVOID", "WAIT"}:
            max_position = 0.0
        else:
            risk_cap = 0.15 if risk <= 35 else 0.10 if risk <= 55 else 0.05
            confidence_cap = (
                0.05 if confidence < 0.60 else 0.10 if confidence < 0.75 else 0.15
            )
            edge_cap = 0.05 if p5 < 0.62 else 0.10 if p5 < 0.70 else 0.15
            reliability_cap = 0.05 if w5 < 0.25 else 0.10 if w5 < 0.60 else 0.15
            max_position = min(risk_cap, confidence_cap, edge_cap, reliability_cap)
            if decision == "WATCH":
                max_position = min(max_position, 0.05)
        return ForecastDecision(
            decision,
            rationale,
            round(confidence, 4),
            round(edge, 4),
            round(downside, 4),
            round(max_position, 4),
            tuple(gates),
        )

    def build_trade_plan(
        self,
        forecast_decision: ForecastDecision,
        *,
        risk_score: float | None,
        current_price: float | None,
        support: float | None,
        resistance: float | None,
        atr: float | None,
    ) -> dict[str, Any]:
        decision = forecast_decision.decision
        price, support_f, resistance_f, atr_f = (
            _finite(current_price),
            _finite(support),
            _finite(resistance),
            _finite(atr),
        )
        if price is None or price <= 0 or decision not in {"BUY_SETUP", "WATCH"}:
            return {
                "action": decision,
                "entry_zone": None,
                "stop_loss": None,
                "targets": (),
                "max_position_pct": 0.0,
                "risk_reward": None,
                "invalidation": (),
                "confirmations": (),
            }
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
            return {
                "action": "WAIT",
                "entry_zone": None,
                "stop_loss": round(stop, 4),
                "targets": (round(first_target, 4), round(second_target, 4)),
                "max_position_pct": 0.0,
                "risk_reward": round(rr, 2),
                "invalidation": ("first target offers <1.5R",),
                "confirmations": (),
            }
        zone_width = max(0.25 * volatility_buffer, price * 0.0025)
        technical_floor = stop + 0.55 * risk_per_share
        entry_low = max(technical_floor, price - zone_width)
        if support_f is not None and stop < support_f < price:
            entry_low = max(entry_low, min(price, support_f))
        return {
            "action": decision,
            "entry_zone": (round(entry_low, 4), round(price, 4)),
            "stop_loss": round(stop, 4),
            "targets": (round(first_target, 4), round(second_target, 4)),
            "max_position_pct": forecast_decision.max_position_fraction,
            "risk_reward": round(rr, 2),
            "invalidation": (f"close below {stop:.4f}",),
            "confirmations": (
                "current price remains inside the risk-bounded entry zone",
                "intraday timing model does not predict a materially better near-term entry",
            ),
        }
