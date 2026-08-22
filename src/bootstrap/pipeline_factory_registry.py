# -*- coding: utf-8 -*-
"""Canonical concrete factory registry for pipeline composition.

This module owns the default constructors and their typed bundle.  It is
intentionally unaware of the historical ``src.core.pipeline`` monkeypatch
surface; that compatibility behavior lives in ``legacy_pipeline_factory_seams``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_provider import DataFetcherManager as _DefaultDataFetcherManager
from data_provider.market_regime_adapter import (
    MarketRegimeAdapter as _DefaultMarketRegimeAdapter,
)
from src.analyzer import GeminiAnalyzer as _DefaultGeminiAnalyzer
from src.notification import NotificationService as _DefaultNotificationService
from src.search_service import SearchService as _DefaultSearchService
from src.services.market_hotspot_service import (
    MarketHotspotService as _DefaultMarketHotspotService,
)
from src.services.market_structure_service import (
    MarketStructureService as _DefaultMarketStructureService,
)
from src.services.social_sentiment_service import (
    SocialSentimentService as _DefaultSocialSentimentService,
)
from src.stock_analyzer import StockTrendAnalyzer as _DefaultStockTrendAnalyzer
from src.storage import get_db as _default_get_db


DEFAULT_PIPELINE_FACTORIES = {
    "get_db": _default_get_db,
    "DataFetcherManager": _DefaultDataFetcherManager,
    "MarketRegimeAdapter": _DefaultMarketRegimeAdapter,
    "StockTrendAnalyzer": _DefaultStockTrendAnalyzer,
    "GeminiAnalyzer": _DefaultGeminiAnalyzer,
    "NotificationService": _DefaultNotificationService,
    "SearchService": _DefaultSearchService,
    "MarketStructureService": _DefaultMarketStructureService,
    "MarketHotspotService": _DefaultMarketHotspotService,
    "SocialSentimentService": _DefaultSocialSentimentService,
}


@dataclass(frozen=True)
class PipelineFactorySet:
    """Resolved concrete constructors used by the pipeline composition root."""

    get_db: Any
    data_fetcher_manager: Any
    market_regime_adapter: Any
    stock_trend_analyzer: Any
    gemini_analyzer: Any
    notification_service: Any
    search_service: Any
    market_structure_service: Any
    market_hotspot_service: Any
    social_sentiment_service: Any


def get_default_pipeline_factory(name: str) -> Any:
    """Return a registered default constructor using the historical key contract."""

    return DEFAULT_PIPELINE_FACTORIES[name]
