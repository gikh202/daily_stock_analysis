from __future__ import annotations

import math
from typing import Any, Optional

from .models import TimingAssessment


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class IntradayTimingModel:
    """Estimate whether waiting is likely to improve the near-term entry."""

    version = "v7.1-intraday-timing.1"
    better_entry_metric = "heuristic_score_v1"

    def assess(self, *, base_status: str, current_price: float, entry_low: float | None, entry_high: float | None, stop_loss: float | None, session_low: float, session_high: float, session_vwap: float | None, last_5m_return_pct: float | None, intraday_volatility_pct: float | None, minutes_since_open: int, probability_up_1d: float | None, probability_up_5d: float | None) -> TimingAssessment:
        status = str(base_status or "").strip().upper()
        if status in {"NO_BUY", "INVALIDATED"}:
            return TimingAssessment(status, 0.0, None, 0.0, 0, "hard blocker from prior plan/risk remains authoritative", True)
        if status == "DATA_UNAVAILABLE":
            return TimingAssessment(
                "DATA_UNAVAILABLE",
                0.0,
                None,
                0.0,
                15,
                "current quote/data quality is not sufficient for execution; keep the session open for a later fresh-data recheck",
                False,
            )
        price = float(current_price)
        width = max(1e-9, float(session_high) - float(session_low))
        range_position = _clamp((price - float(session_low)) / width, 0.0, 1.0)
        vwap = _finite(session_vwap)
        vwap_premium_pct = 0.0 if vwap is None or vwap <= 0 else (price / vwap - 1.0) * 100.0
        momentum = _finite(last_5m_return_pct) or 0.0
        vol = max(0.10, _finite(intraday_volatility_pct) or 0.60)
        p1 = _clamp(_finite(probability_up_1d) or 0.50, 0.02, 0.98)
        p5 = _clamp(_finite(probability_up_5d) or 0.50, 0.02, 0.98)
        better = 0.42 + 0.24 * (range_position - 0.50) + _clamp(vwap_premium_pct/2.0,-0.12,0.16) + _clamp(-momentum/2.0,-0.12,0.16) + 0.10*(0.58-p1) + 0.06*(0.56-p5)
        if entry_high is not None and price > entry_high:
            better += _clamp(0.08 + ((price/entry_high-1.0)*100.0)/4.0, 0.08, 0.22)
        if entry_low is not None and price <= entry_low * 1.002:
            better -= 0.08
        better += _clamp((60.0-float(minutes_since_open))/600.0,-0.05,0.10)
        better = _clamp(better,0.05,0.95)
        expected_improvement = max(0.05, vol*(0.22+0.38*better))
        if entry_high is not None and price > entry_high:
            expected_improvement += min(1.0,max(0.0,(price/entry_high-1.0)*100.0)*0.45)
        expected_improvement = _clamp(expected_improvement,0.05,2.5)
        better_price = price*(1.0-expected_improvement/100.0)
        if stop_loss is not None:
            better_price = max(float(stop_loss)*1.002,better_price)
        falling_hard = momentum <= -0.45
        continuation_strong = bool(momentum>=0.30 and range_position>=0.65 and p1>=0.58 and vwap_premium_pct>=-0.10)
        if status == "BUY_NOW":
            if better>=0.62 and expected_improvement>=0.20 and not continuation_strong:
                return TimingAssessment("WAIT_BETTER_ENTRY",round(better,4),round(better_price,4),round(expected_improvement,4),15 if minutes_since_open<60 else 30,f"near-term better-entry heuristic score {better:.1%} with estimated {expected_improvement:.2f}% price improvement; expected value favors waiting",False)
            return TimingAssessment("BUY_NOW",round(better,4),round(better_price,4),round(expected_improvement,4),0,f"waiting edge is not material (better-entry heuristic score {better:.1%}, estimated improvement {expected_improvement:.2f}%); current setup remains executable",True)
        if status == "WAIT_PULLBACK":
            return TimingAssessment("WAIT_BETTER_ENTRY",round(max(better,0.60),4),round(better_price,4),round(expected_improvement,4),15 if minutes_since_open<60 else 30,"current price is extended above the risk-bounded entry; wait for a better price rather than chase",False)
        if status in {"WAIT_ENTRY","WAIT_STABILIZE"}:
            return TimingAssessment("WAIT_CONFIRMATION",round(better,4),round(better_price,4),round(expected_improvement,4),15 if minutes_since_open<90 else 30,"price is already cheap/weak relative to the plan; "+("downside momentum is still elevated" if falling_hard else "stabilization is not yet confirmed"),False)
        return TimingAssessment(status or "WAIT_CONFIRMATION",round(better,4),round(better_price,4),round(expected_improvement,4),20,"state is non-terminal; re-evaluate with fresher intraday evidence",False)
