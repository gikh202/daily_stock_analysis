from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class EntryCandidate:
    source: str
    price: float
    touch_score: float
    reward_risk: float
    expected_value_score: float
    distance_pct: float


@dataclass(frozen=True)
class EntryOptimization:
    ideal_entry_price: float | None
    acceptable_entry_low: float | None
    acceptable_entry_high: float | None
    no_chase_above: float | None
    candidate_source: str | None
    touch_score: float | None
    expected_value_score: float | None
    candidates: tuple[EntryCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [asdict(item) for item in self.candidates]
        return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _probability(value: Any) -> float:
    number = _finite(value)
    return _clamp(number if number is not None else 0.50, 0.05, 0.95)


class EntryOptimizer:
    """Rank causal intraday entry candidates by risk-bounded expected value.

    Scores are deterministic research scores, not calibrated probabilities.
    The optimizer never weakens the close-plan stop or creates a buy when the
    upstream execution contract is rejected.
    """

    version = "entry-optimizer-v1"

    def optimize(
        self,
        *,
        current_price: float,
        stop_loss: float | None,
        targets: Iterable[float] = (),
        entry_low: float | None = None,
        entry_high: float | None = None,
        session_low: float | None = None,
        session_high: float | None = None,
        session_vwap: float | None = None,
        ema20: float | None = None,
        opening_range_low: float | None = None,
        previous_close: float | None = None,
        intraday_volatility_pct: float | None = None,
        last_5m_return_pct: float | None = None,
        probability_up_1d: float | None = None,
        probability_up_5d: float | None = None,
        expected_return_5d_pct: float | None = None,
        market_regime: str | None = None,
    ) -> EntryOptimization:
        current = _finite(current_price)
        stop = _finite(stop_loss)
        if current is None or current <= 0 or stop is None or stop <= 0 or stop >= current:
            return EntryOptimization(None, None, None, None, None, None, None, ())

        vol = max(0.25, _finite(intraday_volatility_pct) or 0.65)
        momentum = _finite(last_5m_return_pct) or 0.0
        p1 = _probability(probability_up_1d)
        p5 = _probability(probability_up_5d)
        directional_edge = 0.55 * p1 + 0.45 * p5
        regime = str(market_regime or "").strip().lower()

        target_values = sorted(
            value
            for value in (_finite(item) for item in targets)
            if value is not None and value > current
        )
        target1 = target_values[0] if target_values else None
        fallback_return = max(0.50, _finite(expected_return_5d_pct) or 1.0)

        raw: list[tuple[str, float]] = [("current", current)]
        if entry_low is not None:
            raw.append(("plan_entry_low", float(entry_low)))
        if entry_high is not None:
            raw.append(("plan_entry_high", float(entry_high)))
        if entry_low is not None and entry_high is not None:
            raw.append(("plan_entry_mid", (float(entry_low) + float(entry_high)) / 2.0))
        for source, value in (
            ("vwap", session_vwap),
            ("ema20_1m", ema20),
            ("opening_range_low", opening_range_low),
            ("session_low", session_low),
            ("previous_close", previous_close),
        ):
            number = _finite(value)
            if number is not None:
                raw.append((source, number))

        # Keep only causal, risk-valid prices and dedupe near-identical levels.
        clean: list[tuple[str, float]] = []
        for source, price in raw:
            if price <= stop * 1.002 or price > current * 1.003:
                continue
            if any(abs(price / existing - 1.0) < 0.001 for _, existing in clean):
                continue
            clean.append((source, price))
        if not clean:
            return EntryOptimization(None, None, None, None, None, None, None, ())

        session_low_f = _finite(session_low)
        session_high_f = _finite(session_high)
        vwap_f = _finite(session_vwap)
        entry_high_f = _finite(entry_high)
        range_width = (
            max(1e-9, session_high_f - session_low_f)
            if session_low_f is not None and session_high_f is not None
            else None
        )

        candidates: list[EntryCandidate] = []
        for source, price in clean:
            distance_pct = max(0.0, (current / price - 1.0) * 100.0)
            if source == "current":
                touch = 1.0
            else:
                reach_scale = max(0.35, vol * 1.15)
                touch = math.exp(-distance_pct / reach_scale)
                touch += _clamp(-momentum / 4.0, -0.08, 0.08)
                if regime == "risk_off":
                    touch += 0.04
                elif regime == "risk_on":
                    touch -= 0.025
                touch = _clamp(touch, 0.05, 0.98)

            risk_per_share = max(0.01, price - stop)
            reward_per_share = (
                target1 - price
                if target1 is not None and target1 > price
                else price * fallback_return / 100.0
            )
            rr = max(0.0, reward_per_share / risk_per_share)
            rr_score = _clamp(rr / 3.0, 0.0, 1.0)
            improvement_score = _clamp(distance_pct / max(vol, 0.35), 0.0, 1.0)

            extension = 0.0
            if vwap_f is not None and vwap_f > 0 and price > vwap_f:
                extension += _clamp(((price / vwap_f - 1.0) * 100.0) / max(vol, 0.35), 0.0, 1.0)
            if entry_high_f is not None and entry_high_f > 0 and price > entry_high_f:
                extension += _clamp(((price / entry_high_f - 1.0) * 100.0) / max(vol, 0.35), 0.0, 1.0)
            if range_width is not None and session_low_f is not None:
                range_pos = _clamp((price - session_low_f) / range_width, 0.0, 1.0)
                extension += max(0.0, range_pos - 0.65)

            continuation_risk = _clamp(
                max(0.0, directional_edge - 0.50) * 0.8
                + max(0.0, momentum) / 3.0
                + (0.02 if regime == "risk_on" else 0.0),
                0.0,
                0.25,
            )
            quality = (
                0.42 * rr_score
                + 0.28 * improvement_score
                + 0.30 * directional_edge
                - 0.16 * _clamp(extension, 0.0, 1.5)
            )
            ev = touch * quality - (1.0 - touch) * continuation_risk
            candidates.append(
                EntryCandidate(
                    source=source,
                    price=round(price, 4),
                    touch_score=round(touch, 4),
                    reward_risk=round(rr, 3),
                    expected_value_score=round(ev, 4),
                    distance_pct=round(distance_pct, 4),
                )
            )

        candidates.sort(
            key=lambda item: (item.expected_value_score, item.reward_risk, item.touch_score),
            reverse=True,
        )
        best = candidates[0]
        zone_half_pct = _clamp(0.12 * vol, 0.08, 0.35)
        acceptable_low = max(stop * 1.002, best.price * (1.0 - zone_half_pct / 100.0))
        acceptable_high = min(current * 1.003, best.price * (1.0 + zone_half_pct / 100.0))
        chase_buffer_pct = _clamp(0.35 + 0.20 * vol, 0.40, 0.90)
        chase_anchor = max(
            value for value in (acceptable_high, _finite(entry_high), best.price) if value is not None
        )
        no_chase = chase_anchor * (1.0 + chase_buffer_pct / 100.0)

        return EntryOptimization(
            ideal_entry_price=round(best.price, 4),
            acceptable_entry_low=round(acceptable_low, 4),
            acceptable_entry_high=round(acceptable_high, 4),
            no_chase_above=round(no_chase, 4),
            candidate_source=best.source,
            touch_score=best.touch_score,
            expected_value_score=best.expected_value_score,
            candidates=tuple(candidates[:8]),
        )
