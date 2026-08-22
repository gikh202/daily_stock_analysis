# -*- coding: utf-8 -*-
"""Dependency assembly for the stock-analysis pipeline.

This module is the composition root for concrete runtime dependencies used by
``StockAnalysisPipeline``.  Keeping construction here lets the pipeline focus
on orchestration and allows tests to inject light-weight dependency bundles.

Imports of concrete adapters are intentionally local to the builder so merely
importing the dependency contract does not eagerly load the full runtime graph.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


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

    # Local imports keep this composition root from becoming an import-time
    # dependency hub and make the dependency boundary explicit.
    from data_provider import DataFetcherManager
    from data_provider.market_regime_adapter import MarketRegimeAdapter
    from src.analyzer import GeminiAnalyzer
    from src.notification import NotificationService
    from src.search_service import SearchService
    from src.services.market_hotspot_service import MarketHotspotService
    from src.services.market_structure_service import MarketStructureService
    from src.services.social_sentiment_service import SocialSentimentService
    from src.stock_analyzer import StockTrendAnalyzer
    from src.storage import get_db

    db = get_db()
    fetcher_manager = DataFetcherManager()
    market_regime_adapter = MarketRegimeAdapter()
    trend_analyzer = StockTrendAnalyzer()
    analyzer = GeminiAnalyzer(
        config=config,
        skills=list(analysis_skills) if analysis_skills is not None else None,
    )
    notifier = NotificationService(source_message=source_message)
    market_structure_service = MarketStructureService(
        fetcher_manager=fetcher_manager,
    )

    market_hotspot_service = None
    try:
        market_hotspot_service = MarketHotspotService(
            fetcher_manager=fetcher_manager,
        )
    except Exception as exc:
        logger.debug("market hotspot service init failed (fail-open): %s", exc)

    search_service = None
    try:
        search_service = SearchService(
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
        social_sentiment_service = SocialSentimentService(
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
