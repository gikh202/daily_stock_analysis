# -*- coding: utf-8 -*-
"""Compatibility service functions for stock analysis and market review.

The public function API is kept for existing CLI/Bot callers. Concrete
``StockAnalysisPipeline`` construction is delegated to ``pipeline_factory`` so
this module no longer depends directly on the orchestration implementation.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List, Optional

from src.enums import ReportType
from src.services.pipeline_factory import (
    PipelineFactory,
    create_analysis_pipeline,
    get_analysis_config,
)

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult
    from src.config import Config
    from src.notification import NotificationService


def analyze_stock(
    stock_code: str,
    config: Optional["Config"] = None,
    full_report: bool = False,
    notifier: Optional["NotificationService"] = None,
    pipeline_factory: PipelineFactory = create_analysis_pipeline,
) -> Optional["AnalysisResult"]:
    """Analyze one stock while keeping pipeline construction injectable."""

    effective_config = config or get_analysis_config()
    pipeline = pipeline_factory(
        config=effective_config,
        query_id=uuid.uuid4().hex,
        query_source="cli",
    )

    if notifier:
        pipeline.notifier = notifier

    report_type = ReportType.FULL if full_report else ReportType.SIMPLE
    return pipeline.process_single_stock(
        code=stock_code,
        skip_analysis=False,
        single_stock_notify=notifier is not None,
        report_type=report_type,
    )


def analyze_stocks(
    stock_codes: List[str],
    config: Optional["Config"] = None,
    full_report: bool = False,
    notifier: Optional["NotificationService"] = None,
    pipeline_factory: PipelineFactory = create_analysis_pipeline,
) -> List["AnalysisResult"]:
    """Analyze multiple stocks through the same service boundary."""

    effective_config = config or get_analysis_config()
    results: List["AnalysisResult"] = []
    for stock_code in stock_codes:
        result = analyze_stock(
            stock_code,
            effective_config,
            full_report,
            notifier,
            pipeline_factory,
        )
        if result:
            results.append(result)
    return results


def perform_market_review(
    config: Optional["Config"] = None,
    notifier: Optional["NotificationService"] = None,
    pipeline_factory: PipelineFactory = create_analysis_pipeline,
) -> Optional[str]:
    """Run a market review without coupling callers to the concrete pipeline."""

    from src.core.market_review import run_market_review

    effective_config = config or get_analysis_config()
    pipeline = pipeline_factory(
        config=effective_config,
        query_id=uuid.uuid4().hex,
        query_source="cli",
    )
    review_notifier = notifier or pipeline.notifier

    return run_market_review(
        notifier=review_notifier,
        analyzer=pipeline.analyzer,
        search_service=pipeline.search_service,
        config=effective_config,
        trigger_source="service",
    )
