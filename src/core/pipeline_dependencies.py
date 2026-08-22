# -*- coding: utf-8 -*-
"""Dependency assembly for the stock-analysis pipeline.

This module is the composition root for concrete runtime dependencies used by
``StockAnalysisPipeline``. Keeping construction here lets the pipeline focus
on orchestration and allows tests to inject light-weight dependency bundles.

The historical pipeline module exposed its concrete constructors as patch
seams. During this refactor we keep those seams available and resolve them at
build time so existing callers/tests can monkeypatch them without moving
concrete construction back into ``StockAnalysisPipeline.__init__``.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import sys
from typing import Any, Optional, Sequence

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

logger = logging.getLogger(__name__)


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


def _install_legacy_pipeline_seams() -> None:
    """Keep historical ``src.core.pipeline.<constructor>`` patch targets alive."""

    pipeline_module = _pipeline_module()
    if pipeline_module is None:
        return
    for name, default in _DEFAULT_FACTORIES.items():
        if not hasattr(pipeline_module, name):
            setattr(pipeline_module, name, default)


def _resolve_factory(name: str) -> Any:
    """Honor a patched historical pipeline seam, otherwise use the default."""

    pipeline_module = _pipeline_module()
    if pipeline_module is not None and hasattr(pipeline_module, name):
        return getattr(pipeline_module, name)
    return _DEFAULT_FACTORIES[name]


# ``pipeline_dependencies`` is imported while ``src.core.pipeline`` is being
# initialized, so installing the aliases here preserves its previous public
# monkeypatch surface without putting concrete construction back in __init__.
_install_legacy_pipeline_seams()


@dataclass(frozen=True)
class PipelineDependencies:
    """Concrete collaborators required by ``StockAnalysisPipeline``."""

    db: Any
    fetcher_manager: Any
    market_regime_adapter: Any
    trend_analyzer: Any
    analyzer: Any
    notifier: Any
    market_structure_service: Any
    market_hotspot_service: Optional[Any]
    search_service: Optional[Any]
    social_sentiment_service: Optional[Any]


def build_pipeline_dependencies(
    *,
    config: Any,
    source_message: Any = None,
    analysis_skills: Optional[Sequence[str]] = None,
) -> PipelineDependencies:
    """Build the default runtime dependency graph.

    Optional integrations retain the pipeline's existing fail-open behavior:
    hotspot, search, and social-sentiment initialization failures are logged
    and converted to ``None`` instead of aborting stock analysis.
    """

    get_db = _resolve_factory("get_db")
    data_fetcher_manager = _resolve_factory("DataFetcherManager")
    market_regime_adapter_factory = _resolve_factory("MarketRegimeAdapter")
    stock_trend_analyzer = _resolve_factory("StockTrendAnalyzer")
    gemini_analyzer = _resolve_factory("GeminiAnalyzer")
    notification_service = _resolve_factory("NotificationService")
    search_service_factory = _resolve_factory("SearchService")
    market_structure_service_factory = _resolve_factory("MarketStructureService")
    market_hotspot_service_factory = _resolve_factory("MarketHotspotService")
    social_sentiment_service_factory = _resolve_factory("SocialSentimentService")

    db = get_db()
    fetcher_manager = data_fetcher_manager()
    market_regime_adapter = market_regime_adapter_factory()
    trend_analyzer = stock_trend_analyzer()
    analyzer = gemini_analyzer(
        config=config,
        skills=list(analysis_skills) if analysis_skills is not None else None,
    )
    notifier = notification_service(source_message=source_message)
    market_structure_service = market_structure_service_factory(
        fetcher_manager=fetcher_manager,
    )

    market_hotspot_service = None
    try:
        market_hotspot_service = market_hotspot_service_factory(
            fetcher_manager=fetcher_manager,
        )
    except Exception as exc:
        logger.debug("market hotspot service init failed (fail-open): %s", exc)

    search_service = None
    try:
        search_service = search_service_factory(
            bocha_keys=config.bocha_api_keys,
            tavily_keys=config.tavily_api_keys,
            anspire_keys=config.anspire_api_keys,
            brave_keys=config.brave_api_keys,
            serpapi_keys=config.serpapi_keys,
            minimax_keys=config.minimax_api_keys,
            searxng_base_urls=config.searxng_base_urls,
            searxng_public_instances_enabled=config.searxng_public_instances_enabled,
            news_max_age_days=config.news_max_age_days,
            news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
        )
    except Exception as exc:
        logger.warning(
            "搜索服务初始化失败，将以无搜索模式运行: %s",
            exc,
            exc_info=True,
        )

    social_sentiment_service = None
    try:
        social_sentiment_service = social_sentiment_service_factory(
            api_key=config.social_sentiment_api_key,
            api_url=config.social_sentiment_api_url,
        )
    except Exception as exc:
        logger.warning(
            "社交舆情服务初始化失败，将跳过舆情分析: %s",
            exc,
            exc_info=True,
        )

    return PipelineDependencies(
        db=db,
        fetcher_manager=fetcher_manager,
        market_regime_adapter=market_regime_adapter,
        trend_analyzer=trend_analyzer,
        analyzer=analyzer,
        notifier=notifier,
        market_structure_service=market_structure_service,
        market_hotspot_service=market_hotspot_service,
        search_service=search_service,
        social_sentiment_service=social_sentiment_service,
    )