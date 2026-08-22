# -*- coding: utf-8 -*-
"""Characterization tests for optional pipeline dependency assembly."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from src.core.pipeline_optional_dependencies import (
    build_optional_pipeline_dependencies,
)


def _config(**overrides):
    values = {
        "bocha_api_keys": ["bocha"],
        "tavily_api_keys": ["tavily"],
        "anspire_api_keys": ["anspire"],
        "brave_api_keys": ["brave"],
        "serpapi_keys": ["serpapi"],
        "minimax_api_keys": ["minimax"],
        "searxng_base_urls": ["https://search.example"],
        "searxng_public_instances_enabled": True,
        "news_max_age_days": 7,
        "social_sentiment_api_key": "social-key",
        "social_sentiment_api_url": "https://social.example",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RecordingLogger:
    def __init__(self) -> None:
        self.debug_calls = []
        self.warning_calls = []

    def debug(self, *args, **kwargs) -> None:
        self.debug_calls.append((args, kwargs))

    def warning(self, *args, **kwargs) -> None:
        self.warning_calls.append((args, kwargs))


def test_optional_dependencies_preserve_order_and_constructor_arguments() -> None:
    calls = []
    fetcher_manager = object()
    hotspot = object()
    search = object()
    social = object()

    def build_hotspot(**kwargs):
        calls.append(("hotspot", kwargs))
        return hotspot

    def build_search(**kwargs):
        calls.append(("search", kwargs))
        return search

    def build_social(**kwargs):
        calls.append(("social", kwargs))
        return social

    factories = SimpleNamespace(
        market_hotspot_service=build_hotspot,
        search_service=build_search,
        social_sentiment_service=build_social,
    )
    config = _config(news_strategy_profile="swing")
    logger = RecordingLogger()

    result = build_optional_pipeline_dependencies(
        factories=factories,
        config=config,
        fetcher_manager=fetcher_manager,
        logger=logger,
    )

    assert result.market_hotspot_service is hotspot
    assert result.search_service is search
    assert result.social_sentiment_service is social
    assert [name for name, _ in calls] == ["hotspot", "search", "social"]
    assert calls[0][1] == {"fetcher_manager": fetcher_manager}
    assert calls[1][1] == {
        "bocha_keys": ["bocha"],
        "tavily_keys": ["tavily"],
        "anspire_keys": ["anspire"],
        "brave_keys": ["brave"],
        "serpapi_keys": ["serpapi"],
        "minimax_keys": ["minimax"],
        "searxng_base_urls": ["https://search.example"],
        "searxng_public_instances_enabled": True,
        "news_max_age_days": 7,
        "news_strategy_profile": "swing",
    }
    assert calls[2][1] == {
        "api_key": "social-key",
        "api_url": "https://social.example",
    }
    assert logger.debug_calls == []
    assert logger.warning_calls == []


def test_optional_dependencies_fail_open_and_keep_default_profile_and_logs() -> None:
    calls = []

    def fail_hotspot(**kwargs):
        calls.append(("hotspot", kwargs))
        raise RuntimeError("hotspot down")

    def fail_search(**kwargs):
        calls.append(("search", kwargs))
        raise RuntimeError("search down")

    def fail_social(**kwargs):
        calls.append(("social", kwargs))
        raise RuntimeError("social down")

    factories = SimpleNamespace(
        market_hotspot_service=fail_hotspot,
        search_service=fail_search,
        social_sentiment_service=fail_social,
    )
    logger = RecordingLogger()

    result = build_optional_pipeline_dependencies(
        factories=factories,
        config=_config(),
        fetcher_manager="fetcher",
        logger=logger,
    )

    assert result.market_hotspot_service is None
    assert result.search_service is None
    assert result.social_sentiment_service is None
    assert [name for name, _ in calls] == ["hotspot", "search", "social"]
    assert calls[1][1]["news_strategy_profile"] == "short"

    assert logger.debug_calls[0][0][0] == (
        "market hotspot service init failed (fail-open): %s"
    )
    assert str(logger.debug_calls[0][0][1]) == "hotspot down"
    assert logger.warning_calls[0][0][0] == (
        "搜索服务初始化失败，将以无搜索模式运行: %s"
    )
    assert str(logger.warning_calls[0][0][1]) == "search down"
    assert logger.warning_calls[0][1] == {"exc_info": True}
    assert logger.warning_calls[1][0][0] == (
        "社交舆情服务初始化失败，将跳过舆情分析: %s"
    )
    assert str(logger.warning_calls[1][0][1]) == "social down"
    assert logger.warning_calls[1][1] == {"exc_info": True}


def test_pipeline_composition_root_delegates_optional_fail_open_policy() -> None:
    tree = ast.parse(
        Path("src/bootstrap/pipeline_dependencies.py").read_text(encoding="utf-8")
    )

    imported_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    build_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_pipeline_dependencies"
    )

    assert "src.bootstrap.pipeline_optional_dependencies" in imported_modules
    assert not any(isinstance(node, ast.Try) for node in ast.walk(build_function))
    calls = [
        node
        for node in ast.walk(build_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_optional_pipeline_dependencies"
    ]
    assert len(calls) == 1
    keyword_names = {keyword.arg for keyword in calls[0].keywords}
    assert keyword_names == {"factories", "config", "fetcher_manager", "logger"}
