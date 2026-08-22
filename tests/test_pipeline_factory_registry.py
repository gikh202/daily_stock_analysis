# -*- coding: utf-8 -*-
"""Compatibility and architecture tests for pipeline factory resolution."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import pipeline_factory_registry as registry


def test_resolve_pipeline_factory_honors_historical_pipeline_patch(monkeypatch) -> None:
    sentinel = object()
    fake_pipeline = SimpleNamespace(DataFetcherManager=sentinel)
    monkeypatch.setitem(registry.sys.modules, "src.core.pipeline", fake_pipeline)

    assert registry.resolve_pipeline_factory("DataFetcherManager") is sentinel


def test_install_legacy_pipeline_seams_fills_missing_without_overwriting(monkeypatch) -> None:
    sentinel = object()
    fake_pipeline = SimpleNamespace(DataFetcherManager=sentinel)
    monkeypatch.setitem(registry.sys.modules, "src.core.pipeline", fake_pipeline)

    registry.install_legacy_pipeline_seams()

    assert fake_pipeline.DataFetcherManager is sentinel
    assert callable(fake_pipeline.get_db)
    assert callable(fake_pipeline.GeminiAnalyzer)
    assert callable(fake_pipeline.NotificationService)
    assert callable(fake_pipeline.SearchService)


def test_resolve_pipeline_factory_keeps_unknown_name_failure_contract(monkeypatch) -> None:
    monkeypatch.setitem(registry.sys.modules, "src.core.pipeline", SimpleNamespace())

    with pytest.raises(KeyError):
        registry.resolve_pipeline_factory("not-a-pipeline-factory")


def test_pipeline_dependency_composition_root_no_longer_owns_concrete_imports() -> None:
    tree = ast.parse(
        Path("src/core/pipeline_dependencies.py").read_text(encoding="utf-8")
    )
    imported_modules = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    blocked_modules = {
        "data_provider",
        "data_provider.market_regime_adapter",
        "src.analyzer",
        "src.notification",
        "src.search_service",
        "src.services.market_hotspot_service",
        "src.services.market_structure_service",
        "src.services.social_sentiment_service",
        "src.stock_analyzer",
        "src.storage",
    }

    assert imported_modules.isdisjoint(blocked_modules)
    assert "src.core.pipeline_factory_registry" in imported_modules
