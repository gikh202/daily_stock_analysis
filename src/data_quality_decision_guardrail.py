# -*- coding: utf-8 -*-
"""Fail-closed execution guardrail driven only by explicit data quality."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, List, Optional

from src.report_language import normalize_report_language
from src.schemas.decision_action import localize_action_label, normalize_decision_action


ACTIONABLE_ACTIONS = {"buy", "add"}


def apply_data_quality_decision_guardrail(
    result: Any,
    *,
    analysis_context_pack_overview: Optional[Mapping[str, Any]],
    report_language: str = "zh",
) -> List[str]:
    """Downgrade actionable advice when the context pack is explicitly poor.

    This guardrail intentionally runs after market-context guardrails. If a
    more specific guardrail has already softened the execution action to watch,
    this stage is a no-op so the audit trail keeps the real first cause.

    ``result.action`` is the canonical execution action and therefore wins over
    the model's free-form ``operation_advice``. ``decision_type`` is analytical
    metadata, not an executable instruction, so it must never turn ambiguous
    advice into a buy/add action.
    """

    if result is None or not isinstance(analysis_context_pack_overview, Mapping):
        return []

    data_quality = analysis_context_pack_overview.get("data_quality")
    if not isinstance(data_quality, Mapping):
        return []
    quality_level = str(data_quality.get("level") or "").strip().lower()
    if quality_level != "poor":
        return []

    current_action = normalize_decision_action(getattr(result, "action", None))
    if current_action is None:
        current_action = normalize_decision_action(getattr(result, "operation_advice", None))
    if current_action not in ACTIONABLE_ACTIONS:
        return []

    language = normalize_report_language(
        report_language or getattr(result, "report_language", "zh")
    )
    watch_label = localize_action_label("watch", language) or _watch_label(language)
    reason_code = "insufficient_data_quality:poor"
    reason_text = _reason_text(language)

    result.operation_advice = watch_label
    result.action = "watch"
    result.action_label = watch_label
    result.decision_type = "hold"

    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        dashboard = {}
        result.dashboard = dashboard

    phase_decision = dashboard.get("phase_decision")
    if not isinstance(phase_decision, dict):
        phase_decision = {}
        dashboard["phase_decision"] = phase_decision
    phase_decision["immediate_action"] = watch_label
    phase_decision["data_quality_guardrail_reason"] = reason_code
    _append_text(phase_decision, "confidence_reason", reason_text, language=language)
    limitations = phase_decision.get("data_limitations")
    if not isinstance(limitations, list):
        limitations = []
    if reason_text not in limitations:
        limitations.append(reason_text)
    phase_decision["data_limitations"] = limitations[:5]

    stability = dashboard.get("decision_stability")
    if not isinstance(stability, dict):
        stability = {}
        dashboard["decision_stability"] = stability
    existing_reason = str(stability.get("reason") or "").strip()
    stability["applied"] = True
    stability["reason"] = (
        reason_code if not existing_reason else f"{existing_reason}; {reason_code}"
    )
    stability["data_quality_guardrail"] = {
        "before_action": current_action,
        "after_action": "watch",
        "quality_level": quality_level,
        "reason": reason_code,
    }

    return ["action_downgraded_poor_data_quality"]


def _watch_label(language: str) -> str:
    if language == "en":
        return "Watch"
    if language == "ko":
        return "관망"
    return "观望"


def _reason_text(language: str) -> str:
    if language == "en":
        return "Core evidence quality is poor; actionable buy/add advice was downgraded to watch."
    if language == "ko":
        return "핵심 근거 품질이 낮아 매수/추가 매수 동작을 관망으로 하향 조정했습니다."
    return "核心证据质量较差，买入/加仓动作已降级为观望。"


def _append_text(target: dict[str, Any], key: str, value: str, *, language: str) -> None:
    existing = str(target.get(key) or "").strip()
    if not existing:
        target[key] = value
        return
    if value in existing:
        return
    separator = "; " if language == "en" else "；"
    target[key] = f"{existing}{separator}{value}"


__all__ = ["apply_data_quality_decision_guardrail"]
