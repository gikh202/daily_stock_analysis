# -*- coding: utf-8 -*-
"""Fail-open assembly for optional stock-analysis pipeline integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.bootstrap.pipeline_factory_registry import PipelineFactorySet


@dataclass(frozen=True)
class OptionalPipelineDependencies:
    market_hotspot_service: Optional[Any]
    search_service: Optional[Any]
    social_sentiment_service: Optional[Any]


def build_optional_pipeline_dependencies(
    *,
    factories: PipelineFactorySet,
    config: Any,
    fetcher_manager: Any,
    logger: Any,
) -> OptionalPipelineDependencies:
    market_hotspot_service = None
    try:
        market_hotspot_service = factories.market_hotspot_service(
            fetcher_manager=fetcher_manager,
        )
    except Exception as exc:
        logger.debug("market hotspot service init failed (fail-open): %s", exc)

    search_service = None
    try:
        search_service = factories.search_service(
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
        social_sentiment_service = factories.social_sentiment_service(
            api_key=config.social_sentiment_api_key,
            api_url=config.social_sentiment_api_url,
        )
    except Exception as exc:
        logger.warning(
            "社交舆情服务初始化失败，将跳过舆情分析: %s",
            exc,
            exc_info=True,
        )

    return OptionalPipelineDependencies(
        market_hotspot_service=market_hotspot_service,
        search_service=search_service,
        social_sentiment_service=social_sentiment_service,
    )
