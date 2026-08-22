# -*- coding: utf-8 -*-
"""Characterization tests for extracted AnalysisResult integrity policy."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from src.analysis_content_integrity import (
    apply_placeholder_fill,
    check_content_integrity,
)


def _result(**overrides):
    values = {
        "sentiment_score": 60,
        "operation_advice": "hold",
        "analysis_summary": "summary",
        "decision_type": "hold",
        "dashboard": {
            "core_conclusion": {"one_sentence": "summary"},
            "intelligence": {"risk_alerts": []},
            "battle_plan": {"sniper_points": {"stop_loss": "10.0"}},
        },
        "risk_warning": None,
        "report_language": "zh",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_complete_result_passes_without_missing_fields() -> None:
    passed, missing = check_content_integrity(_result())

    assert passed is True
    assert missing == []


def test_missing_result_preserves_historical_field_order() -> None:
    result = _result(
        sentiment_score=None,
        operation_advice=" ",
        analysis_summary="",
        dashboard={},
    )

    passed, missing = check_content_integrity(result, require_phase_decision=True)

    assert passed is False
    assert missing == [
        "sentiment_score",
        "operation_advice",
        "analysis_summary",
        "dashboard.core_conclusion.one_sentence",
        "dashboard.intelligence.risk_alerts",
        "dashboard.battle_plan.sniper_points.stop_loss",
        "dashboard.phase_decision.phase_context",
        "dashboard.phase_decision.action_window",
        "dashboard.phase_decision.immediate_action",
        "dashboard.phase_decision.watch_conditions",
        "dashboard.phase_decision.next_check_time",
        "dashboard.phase_decision.confidence_reason",
        "dashboard.phase_decision.data_limitations",
    ]


def test_placeholder_fill_preserves_defaults_and_risk_warning_flattening() -> None:
    result = _result(
        sentiment_score=None,
        operation_advice="",
        analysis_summary="",
        dashboard={},
        risk_warning=["risk-a", {"kind": "risk-b"}, ["risk-c"]],
    )
    _, missing = check_content_integrity(result, require_phase_decision=True)

    apply_placeholder_fill(result, missing)

    assert result.sentiment_score == 50
    assert result.operation_advice
    assert result.analysis_summary
    assert result.dashboard["core_conclusion"]["one_sentence"]
    assert result.dashboard["intelligence"]["risk_alerts"] == [
        "risk-a",
        '{"kind": "risk-b"}',
        "risk-c",
    ]
    assert result.dashboard["battle_plan"]["sniper_points"]["stop_loss"]
    phase = result.dashboard["phase_decision"]
    assert phase["phase_context"] == {}
    assert phase["watch_conditions"] == []
    assert phase["data_limitations"] == []
    assert phase["action_window"] == "模型未提供阶段化行动窗口"
    assert phase["immediate_action"] == "模型未提供阶段化即时动作"
    assert phase["next_check_time"] == "模型未提供下一次检查点"
    assert phase["confidence_reason"] == "模型未提供阶段化置信度理由"


def test_phase_placeholder_language_remains_deterministic() -> None:
    result = _result(
        report_language="en",
        dashboard={
            "core_conclusion": {"one_sentence": "summary"},
            "intelligence": {"risk_alerts": []},
            "battle_plan": {"sniper_points": {"stop_loss": "10.0"}},
            "phase_decision": {
                "phase_context": {},
                "watch_conditions": [],
                "data_limitations": [],
            },
        },
    )
    _, missing = check_content_integrity(result, require_phase_decision=True)

    apply_placeholder_fill(result, missing)

    phase = result.dashboard["phase_decision"]
    assert phase["action_window"] == "Model did not provide a phase action window"
    assert phase["immediate_action"] == "Model did not provide a phase-aware immediate action"
    assert phase["next_check_time"] == "Model did not provide a next check point"
    assert phase["confidence_reason"] == "Model did not provide a phase confidence rationale"


def test_analyzer_keeps_thin_compatibility_facades() -> None:
    tree = ast.parse(
        Path("src/infrastructure/llm/analyzer_impl.py").read_text(encoding="utf-8")
    )
    imports = {
        alias.name: alias.asname
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.analysis_content_integrity"
        for alias in node.names
    }
    assert imports == {
        "apply_placeholder_fill": "_apply_placeholder_fill_policy",
        "check_content_integrity": "_check_content_integrity_policy",
    }

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"check_content_integrity", "apply_placeholder_fill"}
    }
    assert set(functions) == {"check_content_integrity", "apply_placeholder_fill"}

    check_calls = [
        node
        for node in ast.walk(functions["check_content_integrity"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_check_content_integrity_policy"
    ]
    fill_calls = [
        node
        for node in ast.walk(functions["apply_placeholder_fill"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_apply_placeholder_fill_policy"
    ]
    assert len(check_calls) == 1
    assert len(fill_calls) == 1
