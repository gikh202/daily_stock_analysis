# -*- coding: utf-8 -*-
"""Historical import surface for bootstrap pipeline factory compatibility."""

from __future__ import annotations

import sys

from src.bootstrap.pipeline_factory_registry import PipelineFactorySet
from src.bootstrap import legacy_pipeline_factory_seams as _legacy


def install_legacy_pipeline_seams() -> None:
    return _legacy.install_legacy_pipeline_seams()


def resolve_pipeline_factory(name: str):
    return _legacy.resolve_pipeline_factory(name)


def resolve_pipeline_factories() -> PipelineFactorySet:
    # Intentionally call this module's resolver so existing monkeypatches of
    # src.core.pipeline_factory_registry.resolve_pipeline_factory still work.
    return PipelineFactorySet(
        get_db=resolve_pipeline_factory("get_db"),
        data_fetcher_manager=resolve_pipeline_factory("DataFetcherManager"),
        market_regime_adapter=resolve_pipeline_factory("MarketRegimeAdapter"),
        stock_trend_analyzer=resolve_pipeline_factory("StockTrendAnalyzer"),
        gemini_analyzer=resolve_pipeline_factory("GeminiAnalyzer"),
        notification_service=resolve_pipeline_factory("NotificationService"),
        search_service=resolve_pipeline_factory("SearchService"),
        market_structure_service=resolve_pipeline_factory("MarketStructureService"),
        market_hotspot_service=resolve_pipeline_factory("MarketHotspotService"),
        social_sentiment_service=resolve_pipeline_factory("SocialSentimentService"),
    )


__architecture_forward_to__ = "src.bootstrap.legacy_pipeline_factory_seams"
