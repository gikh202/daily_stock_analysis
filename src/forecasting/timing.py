from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

from .models import TimingAssessment
from .timing_policy import TimingPolicy, load_timing_policy


# Production workflow candidates after the initial 09:30 ET evaluation.
# Values are minutes since the 09:30 open and must stay aligned with
# .github/workflows/01-us-open-confirmation.yml.
PRODUCTION_RECHECK_MINUTES_SINCE_OPEN = (5, 15, 30, 60, 90, 150)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def production_recheck_delay(minutes_since_open: int) -> int:
    """Return minutes until the next configured production recheck.

    The workflow runs at 09:30 / 09:35 / 09:45 / 10:00 / 10:30 / 11:00 /
    12:00 ET. GitHub may start a runner late, so this function uses the actual
    evaluation clock and picks the next still-future nominal candidate. A zero
    result means the automated recheck window is over for the session.
    """
    current = max(0, int(minutes_since_open))
    for target in PRODUCTION_RECHECK_MINUTES_SINCE_OPEN:
        if target > current:
            return target - current
    return 0


class IntradayTimingModel:
    """Estimate whether waiting is likely to improve the near-term entry.

    V7.3 keeps hard risk blockers authoritative for execution, but a NO_BUY risk
    veto is no longer an observation terminal while scheduled intraday checks
    remain. This lets the system keep collecting live evidence without ever
    converting a rejected close plan into a buy authorization.
    """

    version = "v7.3-intraday-timing.1"
    better_entry_metric = "heuristic_score_v1"

    def __init__(
        self,
        policy: TimingPolicy | None = None,
        *,
        policy_path: str | Path | None = None,
    ) -> None:
        self.policy = (policy or load_timing_policy(policy_path)).validate()
        if self.policy.score_model_version != self.better_entry_metric:
            raise ValueError(
                "timing policy score_model_version does not match model metric"
            )

    @property
    def policy_version(self) -> str:
        return self.policy.version

    def assess(
        self,
        *,
        base_status: str,
        current_price: float,
        entry_low: float | None,
        entry_high: float | None,
        stop_loss: float | None,
        session_low: float,
        session_high: float,
        session_vwap: float | None,
        last_5m_return_pct: float | None,
        intraday_volatility_pct: float | None,
        minutes_since_open: int,
        probability_up_1d: float | None,
        probability_up_5d: float | None,
    ) -> TimingAssessment:
        p = self.policy
        status = str(base_status or "").strip().upper()
        scheduled_recheck = production_recheck_delay(minutes_since_open)
        has_scheduled_follow_up = scheduled_recheck > 0

        if status == "INVALIDATED":
            return TimingAssessment(
                status,
                0.0,
                None,
                0.0,
                0,
                "hard invalidation remains authoritative and terminal",
                True,
            )
        if status == "NO_BUY":
            rationale = "hard blocker from prior plan/risk remains authoritative for execution"
            if has_scheduled_follow_up:
                rationale += (
                    f"; observation remains active and the next automated recheck is in "
                    f"{scheduled_recheck} minutes"
                )
            else:
                rationale += "; automated observation window is over for today"
            return TimingAssessment(
                "NO_BUY",
                0.0,
                None,
                0.0,
                scheduled_recheck,
                rationale,
                not has_scheduled_follow_up,
            )
        if status == "DATA_UNAVAILABLE":
            rationale = "current quote/data quality is not sufficient for execution"
            if has_scheduled_follow_up:
                rationale += (
                    f"; next automated production recheck is in {scheduled_recheck} minutes"
                )
            else:
                rationale += "; automated production recheck window is over for today"
            return TimingAssessment(
                "DATA_UNAVAILABLE",
                0.0,
                None,
                0.0,
                scheduled_recheck,
                rationale,
                not has_scheduled_follow_up,
            )

        price = float(current_price)
        width = max(1e-9, float(session_high) - float(session_low))
        range_position = _clamp(
            (price - float(session_low)) / width,
            0.0,
            1.0,
        )
        vwap = _finite(session_vwap)
        vwap_premium_pct = (
            0.0 if vwap is None or vwap <= 0 else (price / vwap - 1.0) * 100.0
        )
        momentum = _finite(last_5m_return_pct) or 0.0
        vol = max(0.10, _finite(intraday_volatility_pct) or 0.60)
        p1 = _clamp(_finite(probability_up_1d) or 0.50, 0.02, 0.98)
        p5 = _clamp(_finite(probability_up_5d) or 0.50, 0.02, 0.98)

        better = (
            p.base_score
            + p.range_position_weight * (range_position - 0.50)
            + _clamp(
                vwap_premium_pct / p.vwap_premium_divisor,
                p.vwap_adjust_min,
                p.vwap_adjust_max,
            )
            + _clamp(
                -momentum / p.momentum_divisor,
                p.momentum_adjust_min,
                p.momentum_adjust_max,
            )
            + p.p1_weight * (p.p1_anchor - p1)
            + p.p5_weight * (p.p5_anchor - p5)
        )
        if entry_high is not None and price > entry_high:
            better += _clamp(
                p.above_entry_base_bonus
                + ((price / entry_high - 1.0) * 100.0)
                / p.above_entry_bonus_divisor,
                p.above_entry_base_bonus,
                p.above_entry_bonus_max,
            )
        if entry_low is not None and price <= entry_low * p.near_entry_ratio:
            better -= p.near_entry_discount
        better += _clamp(
            (p.time_anchor_minutes - float(minutes_since_open))
            / p.time_adjust_divisor,
            p.time_adjust_min,
            p.time_adjust_max,
        )
        better = _clamp(better, p.score_min, p.score_max)

        expected_improvement = max(
            p.improvement_min_pct,
            vol
            * (
                p.improvement_vol_base
                + p.improvement_vol_score_weight * better
            ),
        )
        if entry_high is not None and price > entry_high:
            expected_improvement += min(
                p.above_entry_improvement_max_pct,
                max(0.0, (price / entry_high - 1.0) * 100.0)
                * p.above_entry_improvement_weight,
            )
        expected_improvement = _clamp(
            expected_improvement,
            p.improvement_min_pct,
            p.improvement_max_pct,
        )
        better_price = price * (1.0 - expected_improvement / 100.0)
        if stop_loss is not None:
            better_price = max(float(stop_loss) * p.stop_buffer_ratio, better_price)

        falling_hard = momentum <= p.falling_hard_momentum_pct
        continuation_strong = bool(
            momentum >= p.continuation_momentum_pct
            and range_position >= p.continuation_range_position
            and p1 >= p.continuation_p1
            and vwap_premium_pct >= p.continuation_vwap_premium_min_pct
        )
        recommended_early_recheck = (
            p.early_recheck_minutes
            if minutes_since_open < p.early_recheck_cutoff_minutes
            else p.late_recheck_minutes
        )
        recommended_confirmation_recheck = (
            p.early_recheck_minutes
            if minutes_since_open < p.confirmation_recheck_cutoff_minutes
            else p.late_recheck_minutes
        )

        def scheduled_rationale(text: str, recommended: int) -> str:
            if has_scheduled_follow_up:
                return (
                    f"{text}; policy prefers about {recommended} minutes, while the "
                    f"next automated production recheck is in {scheduled_recheck} minutes"
                )
            return f"{text}; automated production recheck window is over for today"

        if status == "BUY_NOW":
            if (
                better >= p.wait_threshold
                and expected_improvement >= p.min_expected_improvement_pct
                and not continuation_strong
            ):
                return TimingAssessment(
                    "WAIT_BETTER_ENTRY",
                    round(better, 4),
                    round(better_price, 4),
                    round(expected_improvement, 4),
                    scheduled_recheck,
                    scheduled_rationale(
                        f"near-term better-entry heuristic score {better:.1%} with estimated {expected_improvement:.2f}% price improvement; expected value favors waiting",
                        recommended_early_recheck,
                    ),
                    not has_scheduled_follow_up,
                )
            return TimingAssessment(
                "BUY_NOW",
                round(better, 4),
                round(better_price, 4),
                round(expected_improvement, 4),
                0,
                f"waiting edge is not material (better-entry heuristic score {better:.1%}, estimated improvement {expected_improvement:.2f}%); current setup remains executable",
                True,
            )
        if status == "WAIT_PULLBACK":
            return TimingAssessment(
                "WAIT_BETTER_ENTRY",
                round(max(better, p.pullback_score_floor), 4),
                round(better_price, 4),
                round(expected_improvement, 4),
                scheduled_recheck,
                scheduled_rationale(
                    "current price is extended above the risk-bounded entry; wait for a better price rather than chase",
                    recommended_early_recheck,
                ),
                not has_scheduled_follow_up,
            )
        if status in {"WAIT_ENTRY", "WAIT_STABILIZE"}:
            return TimingAssessment(
                "WAIT_CONFIRMATION",
                round(better, 4),
                round(better_price, 4),
                round(expected_improvement, 4),
                scheduled_recheck,
                scheduled_rationale(
                    "price is already cheap/weak relative to the plan; "
                    + (
                        "downside momentum is still elevated"
                        if falling_hard
                        else "stabilization is not yet confirmed"
                    ),
                    recommended_confirmation_recheck,
                ),
                not has_scheduled_follow_up,
            )
        return TimingAssessment(
            status or "WAIT_CONFIRMATION",
            round(better, 4),
            round(better_price, 4),
            round(expected_improvement, 4),
            scheduled_recheck,
            scheduled_rationale(
                "state is non-terminal; re-evaluate with fresher intraday evidence",
                p.default_recheck_minutes,
            ),
            not has_scheduled_follow_up,
        )
