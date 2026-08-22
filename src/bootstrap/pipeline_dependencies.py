# -*- coding: utf-8 -*-
"""Bootstrap composition root for stock-analysis pipeline dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional, Sequence

from src.bootstrap.legacy_pipeline_factory_seams import (
    install_legacy_pipeline_seams,
    resolve_pipeline_factories,
)
from src.bootstrap.pipeline_optional_dependencies import (
    build_optional_pipeline_dependencies,
)

logger = logging.getLogger(__name__)

# Preserve the import-time point at which historical constructor seams become
# available while keeping that compatibility behavior isolated to one adapter.
install_legacy_pipeline_seams()


@dataclass(frozen=True)
class PipelineDependencies:
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
    factories = resolve_pipeline_factories()

    db = factories.get_db()
    fetcher_manager = factories.data_fetcher_manager()
    market_regime_adapter = factories.market_regime_adapter()
    trend_analyzer = factories.stock_trend_analyzer()
    analyzer = factories.gemini_analyzer(
        config=config,
        skills=list(analysis_skills) if analysis_skills is not None else None,
    )
    notifier = factories.notification_service(source_message=source_message)
    market_structure_service = factories.market_structure_service(
        fetcher_manager=fetcher_manager,
    )
    optional_dependencies = build_optional_pipeline_dependencies(
        factories=factories,
        config=config,
        fetcher_manager=fetcher_manager,
        logger=logger,
    )

    return PipelineDependencies(
        db=db,
        fetcher_manager=fetcher_manager,
        market_regime_adapter=market_regime_adapter,
        trend_analyzer=trend_analyzer,
        analyzer=analyzer,
        notifier=notifier,
        market_structure_service=market_structure_service,
        market_hotspot_service=optional_dependencies.market_hotspot_service,
        search_service=optional_dependencies.search_service,
        social_sentiment_service=optional_dependencies.social_sentiment_service,
    )
