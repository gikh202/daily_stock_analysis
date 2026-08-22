# -*- coding: utf-8 -*-
"""Architecture contracts for the analyzer compatibility facade."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import src.analyzer as analyzer
import src.infrastructure.llm.analyzer_impl as analyzer_impl
from src.presentation.policies.chip import (
    fill_chip_structure_if_needed,
    normalize_chip_structure_availability,
)
from src.presentation.policies.price_position import fill_price_position_if_needed
from src.domain.decision.structural import stabilize_decision_with_structure
from src.infrastructure.llm.trend_prompt import _sanitize_trend_analysis_for_prompt


def test_analyzer_public_module_aliases_infrastructure_runtime() -> None:
    assert analyzer is analyzer_impl
    assert analyzer.__architecture_infrastructure_impl__ == (
        "src.infrastructure.llm.analyzer_impl"
    )
    assert analyzer.__name__ == "src.analyzer"
    assert analyzer.__spec__ is not None
    assert analyzer.__spec__.name == "src.analyzer"


def test_analyzer_policy_exports_are_single_runtime_sources_of_truth() -> None:
    assert analyzer.fill_chip_structure_if_needed is fill_chip_structure_if_needed
    assert (
        analyzer.normalize_chip_structure_availability
        is normalize_chip_structure_availability
    )
    assert analyzer.fill_price_position_if_needed is fill_price_position_if_needed
    assert analyzer.stabilize_decision_with_structure is stabilize_decision_with_structure
    assert analyzer._sanitize_trend_analysis_for_prompt is _sanitize_trend_analysis_for_prompt


def test_analyzer_monkeypatch_seam_still_targets_runtime_globals() -> None:
    sentinel = object()
    with patch.object(analyzer, "Router", sentinel):
        assert analyzer_impl.Router is sentinel
        assert analyzer.GeminiAnalyzer.__init__.__globals__["Router"] is sentinel


def test_public_analyzer_class_module_name_is_preserved() -> None:
    assert analyzer.GeminiAnalyzer.__module__ == "src.analyzer"
    assert analyzer.AnalysisResult.__module__ == "src.analyzer"


def test_reload_reexecutes_facade_and_preserves_policy_wiring() -> None:
    reloaded = importlib.reload(analyzer)
    assert reloaded is analyzer
    assert reloaded.__spec__ is not None
    assert reloaded.__spec__.name == "src.analyzer"
    assert reloaded.fill_price_position_if_needed is fill_price_position_if_needed
    assert reloaded.stabilize_decision_with_structure is stabilize_decision_with_structure
