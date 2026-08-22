# -*- coding: utf-8 -*-
"""Tests for the service-to-pipeline dependency boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.enums import ReportType
from src.services.analysis_service import AnalysisService
from src.services.analyzer_service import analyze_stock


class _FakePipeline:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = []
        self.notifier = object()
        self.analyzer = object()
        self.search_service = object()

    def process_single_stock(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def test_compat_analyzer_service_accepts_pipeline_factory() -> None:
    config = SimpleNamespace()
    expected = SimpleNamespace(success=True)
    pipeline = _FakePipeline(expected)
    factory_calls = []

    def factory(**kwargs: Any) -> _FakePipeline:
        factory_calls.append(kwargs)
        return pipeline

    result = analyze_stock(
        "AAPL",
        config=config,
        pipeline_factory=factory,
    )

    assert result is expected
    assert factory_calls[0]["config"] is config
    assert factory_calls[0]["query_source"] == "cli"
    assert pipeline.calls == [
        {
            "code": "AAPL",
            "skip_analysis": False,
            "single_stock_notify": False,
            "report_type": ReportType.SIMPLE,
        }
    ]


def test_analysis_service_injects_config_and_pipeline_factory() -> None:
    config = SimpleNamespace()
    pipeline = _FakePipeline(None)
    factory_calls = []

    def factory(**kwargs: Any) -> _FakePipeline:
        factory_calls.append(kwargs)
        return pipeline

    service = AnalysisService(
        repository=object(),
        config_provider=lambda: config,
        pipeline_factory=factory,
    )

    result = service.analyze_stock(
        "AAPL",
        query_id="query-1",
        trace_id="trace-1",
        send_notification=False,
        query_source="api",
    )

    assert result is None
    assert service.last_error == "分析股票 AAPL 返回空结果"
    assert factory_calls == [
        {
            "config": config,
            "query_id": "query-1",
            "trace_id": "trace-1",
            "query_source": "api",
            "progress_callback": None,
            "analysis_skills": None,
            "analysis_phase": "auto",
            "portfolio_context": None,
        }
    ]
    assert pipeline.calls == [
        {
            "code": "AAPL",
            "skip_analysis": False,
            "single_stock_notify": False,
            "report_type": ReportType.FULL,
        }
    ]


def _imported_modules(path: str) -> set[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_service_modules_do_not_import_concrete_pipeline() -> None:
    for path in (
        "src/services/analyzer_service.py",
        "src/services/analysis_service.py",
    ):
        assert "src.core.pipeline" not in _imported_modules(path)
