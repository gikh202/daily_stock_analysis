# -*- coding: utf-8 -*-
"""Stable construction boundary for the stock-analysis pipeline.

Service/API/Bot callers should depend on the small protocols in this module
instead of importing ``src.core.pipeline`` directly.  The concrete pipeline is
loaded lazily so importing a service does not pull data providers, LLM clients,
notification adapters, and storage into the import graph.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class AnalysisPipeline(Protocol):
    """Minimal pipeline surface required by service-layer callers."""

    notifier: Any
    analyzer: Any
    search_service: Any

    def process_single_stock(
        self,
        *,
        code: str,
        skip_analysis: bool,
        single_stock_notify: bool,
        report_type: Any,
    ) -> Any:
        """Run one stock-analysis request."""


class PipelineFactory(Protocol):
    """Factory contract used to inject the concrete analysis pipeline."""

    def __call__(self, **kwargs: Any) -> AnalysisPipeline:
        """Build one analysis-pipeline instance."""


ConfigProvider = Callable[[], Any]


def get_analysis_config() -> Any:
    """Return the application config without creating an import-time dependency."""

    from src.config import get_config

    return get_config()


def create_analysis_pipeline(**kwargs: Any) -> AnalysisPipeline:
    """Create the concrete pipeline behind a lazy service-layer boundary.

    Keeping the import local is intentional: ``src.core.pipeline`` currently
    coordinates storage, market-data providers, LLM analysis, search and
    notifications.  Callers that only need the service API should not import
    that full dependency graph until an analysis actually starts.
    """

    from src.core.pipeline import StockAnalysisPipeline

    return StockAnalysisPipeline(**kwargs)
