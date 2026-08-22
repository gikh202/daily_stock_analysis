# -*- coding: utf-8 -*-
"""Factory registry and legacy patch seams for pipeline dependency assembly.

This module isolates the compatibility bridge that keeps historical
``src.core.pipeline.<constructor>`` monkeypatch targets working while concrete
runtime construction remains outside ``StockAnalysisPipeline``.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

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


_DEFAULT_FACTORIES = {
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


def _pipeline_module() -> Optional[Any]:
    """Return the importing pipeline module when it is present."""

    return sys.modules.get("src.core.pipeline")


def install_legacy_pipeline_seams() -> None:
    """Keep historical ``src.core.pipeline.<constructor>`` patch targets alive."""

    pipeline_module = _pipeline_module()
    if pipeline_module is None:
        return
    for name, default in _DEFAULT_FACTORIES.items():
        if not hasattr(pipeline_module, name):
            setattr(pipeline_module, name, default)


def resolve_pipeline_factory(name: str) -> Any:
    """Honor a patched historical pipeline seam, otherwise use the default."""

    pipeline_module = _pipeline_module()
    if pipeline_module is not None and hasattr(pipeline_module, name):
        return getattr(pipeline_module, name)
    return _DEFAULT_FACTORIES[name]
