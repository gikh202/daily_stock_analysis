# -*- coding: utf-8 -*-
"""Characterization tests for the extracted pipeline volume-price stage."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

from src.core.stages.volume_price import VolumePriceFeaturesStage


def _complete_daily_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-07-01", periods=21, freq="D"),
            "close": [100.0] * 20 + [101.0],
            "volume": [100.0] * 20 + [150.0],
        }
    )


def test_volume_price_stage_preserves_complete_daily_bar_contract() -> None:
    features = VolumePriceFeaturesStage.run(_complete_daily_bars())

    assert features == {
        "trade_date": "2026-07-21T00:00:00",
        "rvol5": 1.5,
        "rvol20": 1.5,
        "volume_ma5": 110.0,
        "volume_ma20": 102.0,
        "volume_trend_5d_pct": 10.0,
        "volume_trend_vs20_pct": 7.32,
        "dollar_volume_proxy": 15150.0,
        "price_change_pct": 1.0,
        "volume_regime": "显著放量",
        "price_volume_signal": "上涨放量-多头确认增强",
        "source": "complete_daily_bars",
    }


def test_volume_price_stage_sorts_dates_without_mutating_input() -> None:
    frame = _complete_daily_bars().iloc[::-1].reset_index(drop=True)
    original = frame.copy(deep=True)

    features = VolumePriceFeaturesStage.run(frame)

    assert features is not None
    assert features["trade_date"] == "2026-07-21T00:00:00"
    assert features["rvol20"] == 1.5
    pd.testing.assert_frame_equal(frame, original)


def test_volume_price_stage_returns_none_for_unusable_input() -> None:
    assert VolumePriceFeaturesStage.run(pd.DataFrame()) is None
    assert VolumePriceFeaturesStage.run(pd.DataFrame({"close": [1.0, 2.0]})) is None
    assert (
        VolumePriceFeaturesStage.run(
            pd.DataFrame(
                {
                    "close": [None, 2.0],
                    "volume": [100.0, 200.0],
                }
            )
        )
        is None
    )


def test_pipeline_volume_price_entrypoint_stays_a_thin_stage_delegate() -> None:
    tree = ast.parse(Path("src/core/pipeline.py").read_text(encoding="utf-8"))
    pipeline_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StockAnalysisPipeline"
    )
    method = next(
        node
        for node in pipeline_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_volume_price_features"
    )

    assert any(isinstance(decorator, ast.Name) and decorator.id == "staticmethod" for decorator in method.decorator_list)

    call_names = set()
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            call_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            call_names.add(node.func.attr)

    assert "run" in call_names
    assert call_names.isdisjoint({"copy", "to_numeric", "dropna", "tail", "mean"})

    stage_references = [
        node.id
        for node in ast.walk(method)
        if isinstance(node, ast.Name) and node.id == "VolumePriceFeaturesStage"
    ]
    assert stage_references
