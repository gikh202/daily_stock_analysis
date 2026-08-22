# -*- coding: utf-8 -*-
"""Tests for the pipeline composition-root boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from src.core.pipeline import StockAnalysisPipeline
from src.core.pipeline_dependencies import PipelineDependencies


class _SearchServiceStub:
    is_available = False


class _SocialSentimentStub:
    is_available = True


def _config_stub() -> SimpleNamespace:
    return SimpleNamespace(
        max_workers=3,
        save_context_snapshot=True,
        daily_market_context_enabled=True,
        enable_realtime_quote=False,
        realtime_source_priority=[],
        enable_chip_distribution=False,
    )


def test_pipeline_accepts_prebuilt_dependencies_without_reassembly() -> None:
    collaborators = {
        "db": object(),
        "fetcher_manager": object(),
        "market_regime_adapter": object(),
        "trend_analyzer": object(),
        "analyzer": object(),
        "notifier": object(),
        "market_structure_service": object(),
        "market_hotspot_service": object(),
        "search_service": _SearchServiceStub(),
        "social_sentiment_service": _SocialSentimentStub(),
    }
    dependencies = PipelineDependencies(**collaborators)

    pipeline = StockAnalysisPipeline(
        config=_config_stub(),
        query_source="test",
        dependencies=dependencies,
    )

    assert pipeline.db is collaborators["db"]
    assert pipeline.fetcher_manager is collaborators["fetcher_manager"]
    assert pipeline._market_regime_adapter is collaborators["market_regime_adapter"]
    assert pipeline.trend_analyzer is collaborators["trend_analyzer"]
    assert pipeline.analyzer is collaborators["analyzer"]
    assert pipeline.notifier is collaborators["notifier"]
    assert pipeline.market_structure_service is collaborators["market_structure_service"]
    assert pipeline.market_hotspot_service is collaborators["market_hotspot_service"]
    assert pipeline.search_service is collaborators["search_service"]
    assert pipeline.social_sentiment_service is collaborators["social_sentiment_service"]


def test_pipeline_init_delegates_concrete_assembly_to_composition_root() -> None:
    tree = ast.parse(Path("src/core/pipeline.py").read_text(encoding="utf-8"))
    pipeline_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StockAnalysisPipeline"
    )
    init_method = next(
        node
        for node in pipeline_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )

    call_names = set()
    for node in ast.walk(init_method):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            call_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            call_names.add(node.func.attr)

    assert "build_pipeline_dependencies" in call_names
    assert call_names.isdisjoint(
        {
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
        }
    )
