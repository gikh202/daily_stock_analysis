# -*- coding: utf-8 -*-
"""Explicit adapter for historical pipeline constructor monkeypatch seams.

Only this bootstrap adapter is allowed to inspect ``src.core.pipeline`` at
runtime.  New code should inject factories/dependencies instead of patching the
legacy module surface.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from src.bootstrap.pipeline_factory_registry import (
    DEFAULT_PIPELINE_FACTORIES,
    PipelineFactorySet,
    get_default_pipeline_factory,
)


def _pipeline_module() -> Optional[Any]:
    return sys.modules.get("src.core.pipeline")


def install_legacy_pipeline_seams() -> None:
    """Populate missing historical constructor attributes without overwriting patches."""

    pipeline_module = _pipeline_module()
    if pipeline_module is None:
        return
    for name, default in DEFAULT_PIPELINE_FACTORIES.items():
        if not hasattr(pipeline_module, name):
            setattr(pipeline_module, name, default)


def resolve_pipeline_factory(name: str) -> Any:
    """Honor a patched historical pipeline seam, otherwise use the canonical default."""

    pipeline_module = _pipeline_module()
    if pipeline_module is not None and hasattr(pipeline_module, name):
        return getattr(pipeline_module, name)
    return get_default_pipeline_factory(name)


def resolve_pipeline_factories() -> PipelineFactorySet:
    """Resolve the complete factory set in the historical construction order."""

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
