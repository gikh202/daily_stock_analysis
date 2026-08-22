# -*- coding: utf-8 -*-
"""Characterization tests for the extracted chip-presentation policy."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.analyzer import (
    _build_chip_structure_from_data as legacy_build_chip_structure_from_data,
    _derive_chip_health as legacy_derive_chip_health,
    _has_meaningful_chip_data as legacy_has_meaningful_chip_data,
    fill_chip_structure_if_needed as legacy_fill_chip_structure_if_needed,
    normalize_chip_structure_availability as legacy_normalize_chip_structure_availability,
)
from src.chip_presentation_policy import (
    _build_chip_structure_from_data,
    _derive_chip_health,
    _has_meaningful_chip_data,
    fill_chip_structure_if_needed,
    normalize_chip_structure_availability,
)


def _result(*, language: str = "zh", dashboard=None):
    return SimpleNamespace(
        report_language=language,
        dashboard=deepcopy(dashboard) if dashboard is not None else {},
    )


@pytest.mark.parametrize(
    "profit_ratio, concentration_90, language",
    [
        (0.95, 0.10, "zh"),
        (0.50, 0.30, "zh"),
        (0.50, 0.10, "zh"),
        (0.20, 0.10, "zh"),
        (0.50, 0.10, "en"),
        (0.50, 0.30, "ko"),
    ],
)
def test_chip_health_policy_matches_legacy(
    profit_ratio,
    concentration_90,
    language,
) -> None:
    assert _derive_chip_health(
        profit_ratio,
        concentration_90,
        language,
    ) == legacy_derive_chip_health(
        profit_ratio,
        concentration_90,
        language,
    )


@pytest.mark.parametrize(
    "chip_data",
    [
        None,
        {},
        {"avg_cost": 0, "concentration_90": 0.1},
        {"avg_cost": 100},
        {"avg_cost": 100, "concentration_90": 0.0},
        {"avg_cost": 100, "concentration_70": 0.0},
        {"avg_cost": "100", "concentration": "0.18"},
        {"avg_cost": float("nan"), "concentration_90": 0.1},
        SimpleNamespace(avg_cost=100, concentration_90=0.12, concentration_70=None),
        SimpleNamespace(avg_cost=100, concentration_90=None, concentration_70=0.08),
    ],
)
def test_meaningful_chip_data_policy_matches_legacy(chip_data) -> None:
    assert _has_meaningful_chip_data(chip_data) == legacy_has_meaningful_chip_data(
        chip_data
    )


@pytest.mark.parametrize(
    "chip_data, language",
    [
        (
            {
                "profit_ratio": 0.55,
                "avg_cost": 98.5,
                "concentration_90": 0.12,
            },
            "zh",
        ),
        (
            {
                "profit_ratio": "0.95",
                "avg_cost": "101.20",
                "concentration_90": "0.30",
            },
            "en",
        ),
        (
            SimpleNamespace(
                profit_ratio=0.40,
                avg_cost=88.0,
                concentration_90=0.10,
            ),
            "ko",
        ),
        (
            {
                "profit_ratio": None,
                "avg_cost": 0,
                "concentration_90": None,
            },
            "zh",
        ),
    ],
)
def test_chip_structure_builder_matches_legacy(chip_data, language) -> None:
    assert _build_chip_structure_from_data(
        chip_data,
        language,
    ) == legacy_build_chip_structure_from_data(chip_data, language)


@pytest.mark.parametrize("language", ["zh", "en", "ko"])
def test_disabled_chip_availability_matches_legacy(language) -> None:
    dashboard = {
        "data_perspective": {
            "chip_structure": {"profit_ratio": "99.0%"},
            "other": "keep",
        }
    }
    new_result = _result(language=language, dashboard=dashboard)
    legacy_result = _result(language=language, dashboard=dashboard)

    normalize_chip_structure_availability(
        new_result,
        None,
        feature_enabled=False,
    )
    legacy_normalize_chip_structure_availability(
        legacy_result,
        None,
        feature_enabled=False,
    )

    assert new_result.dashboard == legacy_result.dashboard
    assert new_result.dashboard["data_perspective"]["chip_structure"] == {}
    assert new_result.dashboard["data_perspective"]["other"] == "keep"


@pytest.mark.parametrize("language", ["zh", "en", "ko"])
def test_unavailable_chip_availability_matches_legacy(language) -> None:
    dashboard = {"data_perspective": {"chip_structure": None}}
    new_result = _result(language=language, dashboard=dashboard)
    legacy_result = _result(language=language, dashboard=dashboard)

    normalize_chip_structure_availability(new_result, None)
    legacy_normalize_chip_structure_availability(legacy_result, None)

    assert new_result.dashboard == legacy_result.dashboard
    assert new_result.dashboard["data_perspective"]["chip_structure"] == {}
    assert new_result.dashboard["data_perspective"]["chip_unavailable_reason"]


def test_valid_chip_availability_fills_placeholders_and_preserves_extra_fields() -> None:
    dashboard = {
        "data_perspective": {
            "chip_structure": {
                "profit_ratio": "N/A",
                "avg_cost": 97.0,
                "concentration": "数据缺失",
                "chip_health": None,
                "provider_note": "keep-me",
            }
        }
    }
    chip_data = {
        "profit_ratio": 0.55,
        "avg_cost": 98.5,
        "concentration_90": 0.12,
    }
    new_result = _result(dashboard=dashboard)
    legacy_result = _result(dashboard=dashboard)

    normalize_chip_structure_availability(new_result, chip_data)
    legacy_normalize_chip_structure_availability(legacy_result, chip_data)

    assert new_result.dashboard == legacy_result.dashboard
    chip_structure = new_result.dashboard["data_perspective"]["chip_structure"]
    assert chip_structure["profit_ratio"] == "55.0%"
    assert chip_structure["avg_cost"] == 97.0
    assert chip_structure["concentration"] == "12.00%"
    assert chip_structure["provider_note"] == "keep-me"


def test_fill_chip_structure_noops_for_non_meaningful_data() -> None:
    dashboard = {
        "data_perspective": {
            "chip_structure": {"profit_ratio": "N/A"},
        }
    }
    chip_data = {"avg_cost": None, "concentration_90": None}
    new_result = _result(dashboard=dashboard)
    legacy_result = _result(dashboard=dashboard)

    fill_chip_structure_if_needed(new_result, chip_data)
    legacy_fill_chip_structure_if_needed(legacy_result, chip_data)

    assert new_result.dashboard == legacy_result.dashboard == dashboard
