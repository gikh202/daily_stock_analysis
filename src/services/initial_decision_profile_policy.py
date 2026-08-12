# -*- coding: utf-8 -*-
"""Shared DecisionProfilePolicy bridge for fresh automatic DecisionSignal writes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from src.schemas.decision_action import localize_action_label, normalize_decision_action
from src.services.decision_profile_policy import (
    PROFILE_POLICY_VERSION,
    DecisionSignalCandidate,
    apply_decision_profile_policy,
)
from src.services.decision_signal_data_quality import normalize_decision_signal_data_quality
from src.utils.sniper_points import extract_sniper_points


INITIAL_SIGNAL_GENERATION_VERSION = "decision-profile-initial-v2"
_ALLOWED_HORIZONS = frozenset({"intraday", "1d", "3d", "5d", "10d", "swing", "long"})
_CONFIDENCE_MAP = {
    "高": 0.8,
    "high": 0.8,
    "中": 0.6,
    "medium": 0.6,
    "mid": 0.6,
    "低": 0.4,
    "low": 0.4,
}


@dataclass(frozen=True)
class InitialDecisionProfilePolicyOutcome:
    """Validated shared-policy outcome consumed by the fresh signal extractor."""

    action: str
    action_label: Optional[str]
    data_quality_level: str
    guardrail_reason: Optional[str]
    data_quality_guardrail_reason: Optional[str]
    guardrail_result: dict[str, object]
    scoring_breakdown: dict[str, object]
    warnings: list[dict[str, object]]
    blocked_reason: Optional[str]
    profile_policy_version: str = PROFILE_POLICY_VERSION
    signal_generation_version: str = INITIAL_SIGNAL_GENERATION_VERSION

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


def apply_initial_decision_profile_policy(
    result: Any,
    *,
    context_snapshot: Optional[Mapping[str, Any]],
    canonical_action: Any,
    score: Optional[int],
    report_language: Optional[str],
    profile_source: str,
) -> Optional[InitialDecisionProfilePolicyOutcome]:
    """Apply the same policy used by reassessment to fresh production signals.

    Only the authoritative fresh persistence path (``auto_default`` with a
    context snapshot) is migrated. Standalone helpers and legacy/backfill paths
    keep their historical behavior so old records remain reproducible.
    """

    if profile_source != "auto_default" or context_snapshot is None:
        return None

    action = normalize_decision_action(canonical_action)
    if action is None:
        return None

    snapshot = _as_mapping(context_snapshot)
    dashboard = _as_mapping(getattr(result, "dashboard", None))
    quality_payload = _as_mapping(snapshot.get("analysis_context_pack_overview")).get(
        "data_quality"
    )
    data_quality_level = normalize_decision_signal_data_quality(quality_payload)

    sniper_points = extract_sniper_points(result)
    entry_low, entry_high = _entry_range(
        sniper_points.get("ideal_buy"),
        sniper_points.get("secondary_buy"),
    )
    market_phase = _extract_market_phase(snapshot, result, dashboard)
    horizon = _extract_horizon(result, dashboard, market_phase, action)
    invalidation = _extract_invalidation(result, dashboard)

    policy = apply_decision_profile_policy(
        DecisionSignalCandidate(
            action=action,
            score=score,
            confidence=_confidence_from_level(getattr(result, "confidence_level", None)),
            horizon=horizon,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=sniper_points.get("stop_loss"),
            target_price=sniper_points.get("take_profit"),
            invalidation=invalidation,
            market_phase=market_phase,
        ),
        decision_profile="balanced",
        data_quality_level=data_quality_level,
    )

    violations = list(policy.guardrail_result.violations)
    data_quality_guardrail_reason = None
    if "insufficient_data_quality" in violations:
        data_quality_guardrail_reason = f"insufficient_data_quality:{data_quality_level}"

    guardrail_reason = data_quality_guardrail_reason
    if guardrail_reason is None and policy.guardrail_result.adjusted and violations:
        guardrail_reason = "decision_profile_policy:" + ",".join(violations)

    final_action = policy.candidate.action
    return InitialDecisionProfilePolicyOutcome(
        action=final_action,
        action_label=localize_action_label(final_action, report_language),
        data_quality_level=data_quality_level,
        guardrail_reason=guardrail_reason,
        data_quality_guardrail_reason=data_quality_guardrail_reason,
        guardrail_result=policy.guardrail_result.as_dict(),
        scoring_breakdown=dict(policy.scoring_breakdown),
        warnings=list(policy.warnings),
        blocked_reason=policy.blocked_reason,
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _confidence_from_level(value: Any) -> Optional[float]:
    return _CONFIDENCE_MAP.get(str(value or "").strip().lower())


def _entry_range(
    ideal_buy: Optional[float],
    secondary_buy: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    if ideal_buy is not None and secondary_buy is not None and ideal_buy > secondary_buy:
        return secondary_buy, ideal_buy
    return ideal_buy, secondary_buy


def _extract_market_phase(
    snapshot: Mapping[str, Any],
    result: Any,
    dashboard: Mapping[str, Any],
) -> Optional[str]:
    snapshot_phase = _as_mapping(snapshot.get("market_phase_summary")).get("phase")
    result_phase = _as_mapping(getattr(result, "market_phase_summary", None)).get("phase")
    phase_decision = _as_mapping(dashboard.get("phase_decision"))
    return _first_text(snapshot_phase, result_phase, phase_decision.get("phase"))


def _extract_horizon(
    result: Any,
    dashboard: Mapping[str, Any],
    market_phase: Optional[str],
    action: str,
) -> str:
    phase_decision = _as_mapping(dashboard.get("phase_decision"))
    battle_plan = _as_mapping(dashboard.get("battle_plan"))
    explicit = _first_text(
        getattr(result, "horizon", None),
        getattr(result, "holding_period", None),
        phase_decision.get("horizon"),
        battle_plan.get("horizon"),
    )
    if explicit in _ALLOWED_HORIZONS:
        return explicit
    return "intraday" if action == "alert" or market_phase == "intraday" else "3d"


def _extract_invalidation(
    result: Any,
    dashboard: Mapping[str, Any],
) -> Optional[str]:
    phase_decision = _as_mapping(dashboard.get("phase_decision"))
    battle_plan = _as_mapping(dashboard.get("battle_plan"))
    return _first_text(
        getattr(result, "invalidation", None),
        getattr(result, "invalid_condition", None),
        phase_decision.get("invalidation"),
        battle_plan.get("invalidation"),
    )


__all__ = [
    "INITIAL_SIGNAL_GENERATION_VERSION",
    "InitialDecisionProfilePolicyOutcome",
    "apply_initial_decision_profile_policy",
]
