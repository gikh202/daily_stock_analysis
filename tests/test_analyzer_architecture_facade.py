# -*- coding: utf-8 -*-
"""Architecture contracts for the analyzer compatibility facade."""

from __future__ import annotations

from unittest.mock import patch

import src.analyzer as analyzer
import src.legacy.analyzer_impl as analyzer_impl
from src.chip_presentation_policy import (
    fill_chip_structure_if_needed,
    normalize_chip_structure_availability,
)
from src.price_position_policy import fill_price_position_if_needed
from src.structural_decision_policy import stabilize_decision_with_structure
from src.trend_prompt_policy import _sanitize_trend_analysis_for_prompt


def test_analyzer_public_module_aliases_isolated_runtime() -> None:
    assert analyzer is analyzer_impl
    assert analyzer.__architecture_legacy_impl__ == "src.legacy.analyzer_impl"
    assert analyzer.__name__ == "src.analyzer"


def test_analyzer_policy_exports_are_single_runtime_sources_of_truth() -> None:
    assert analyzer.fill_chip_structure_if_needed is fill_chip_structure_if_needed
    assert (
        analyzer.normalize_chip_structure_availability
        is normalize_chip_structure_availability
    )
    assert analyzer.fill_price_position_if_needed is fill_price_position_if_needed
    assert analyzer.stabilize_decision_with_structure is stabilize_decision_with_structure
    assert (
        analyzer._sanitize_trend_analysis_for_prompt
        is _sanitize_trend_analysis_for_prompt
    )


def test_analyzer_legacy_monkeypatch_seam_still_targets_runtime_globals() -> None:
    sentinel = object()
    with patch.object(analyzer, "Router", sentinel):
        assert analyzer_impl.Router is sentinel
        assert analyzer.GeminiAnalyzer.__init__.__globals__["Router"] is sentinel


def test_public_analyzer_class_module_name_is_preserved() -> None:
    assert analyzer.GeminiAnalyzer.__module__ == "src.analyzer"
    assert analyzer.AnalysisResult.__module__ == "src.analyzer"
