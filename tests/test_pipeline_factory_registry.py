# -*- coding: utf-8 -*-
"""Compatibility and architecture tests for bootstrap pipeline factories."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import pipeline_factory_registry as registry
from src.bootstrap import pipeline_factory_registry as canonical_registry


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
    assert factories.gemini_analyzer == "factory:GeminiAnalyzer"
    assert factories.social_sentiment_service == "factory:SocialSentimentService"


def test_canonical_factory_registry_has_no_reverse_pipeline_dependency() -> None:
    path = Path("src/bootstrap/pipeline_factory_registry.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "src.core.pipeline" not in source
    assert not any(
        isinstance(node, ast.Import) and any(alias.name == "sys" for alias in node.names)
        for node in tree.body
    )
    assert canonical_registry.get_default_pipeline_factory("GeminiAnalyzer") is not None


def test_only_explicit_legacy_adapter_owns_pipeline_module_lookup() -> None:
    legacy_source = Path(
        "src/bootstrap/legacy_pipeline_factory_seams.py"
    ).read_text(encoding="utf-8")
    assert 'sys.modules.get("src.core.pipeline")' in legacy_source

    for path in (
        Path("src/bootstrap/pipeline_factory_registry.py"),
        Path("src/bootstrap/pipeline_dependencies.py"),
        Path("src/bootstrap/pipeline_optional_dependencies.py"),
    ):
        assert "src.core.pipeline" not in path.read_text(encoding="utf-8")


def test_pipeline_composition_root_consumes_factory_bundle() -> None:
    source = Path("src/bootstrap/pipeline_dependencies.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.bootstrap.legacy_pipeline_factory_seams"
        for alias in node.names
    }
    assert "resolve_pipeline_factories" in imported_names
    assert "resolve_pipeline_factory" not in imported_names
    assert "factories = resolve_pipeline_factories()" in source
