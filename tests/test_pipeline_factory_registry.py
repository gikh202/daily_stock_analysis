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


def test_resolve_pipeline_factories_preserves_historical_resolution_order(monkeypatch) -> None:
    resolved_names = []

    def fake_resolver(name: str):
        resolved_names.append(name)
        return f"factory:{name}"

    monkeypatch.setattr(registry, "resolve_pipeline_factory", fake_resolver)

    factories = registry.resolve_pipeline_factories()

    assert resolved_names == [
        "get_db",
        "DataFetcherManager",
        "MarketRegimeAdapter",
        "StockTrendAnalyzer",
        "GeminiAnalyzer",
        "NotificationService",
        "SearchService",
        "MarketStructureService",
        "MarketHotspotService",
        "SocialSentimentService",
    ]
    assert factories.get_db == "factory:get_db"
    assert factories.data_fetcher_manager == "factory:DataFetcherManager"
    assert factories.market_regime_adapter == "factory:MarketRegimeAdapter"
    assert factories.stock_trend_analyzer == "factory:StockTrendAnalyzer"
    assert factories.gemini_analyzer == "factory:GeminiAnalyzer"
    assert factories.notification_service == "factory:NotificationService"
    assert factories.search_service == "factory:SearchService"
    assert factories.market_structure_service == "factory:MarketStructureService"
    assert factories.market_hotspot_service == "factory:MarketHotspotService"
    assert factories.social_sentiment_service == "factory:SocialSentimentService"


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


def test_pipeline_dependency_composition_root_consumes_factory_bundle() -> None:
    tree = ast.parse(
        Path("src/core/pipeline_dependencies.py").read_text(encoding="utf-8")
    )
    imported_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.core.pipeline_factory_registry"
        for alias in node.names
    }

    assert "resolve_pipeline_factories" in imported_names
    assert "resolve_pipeline_factory" not in imported_names

    source = Path("src/core/pipeline_dependencies.py").read_text(encoding="utf-8")
    assert "factories = resolve_pipeline_factories()" in source
    assert 'resolve_pipeline_factory("' not in source
