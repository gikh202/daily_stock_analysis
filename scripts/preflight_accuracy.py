# -*- coding: utf-8 -*-
"""Fail-fast regression checks for the stock-analysis GitHub Actions path.

No external API request is made.
"""
from __future__ import annotations

import ast
import os
import py_compile
from pathlib import Path
from types import SimpleNamespace


def _compile_critical_files(root: Path) -> None:
    targets = [
        root / "src" / "analyzer.py",
        root / "src" / "search_service.py",
        root / "src" / "core" / "pipeline.py",
        root / "data_provider" / "base.py",
        root / "data_provider" / "market_regime_adapter.py",
    ]
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        source = target.read_text(encoding="utf-8")
        ast.parse(source, filename=str(target), feature_version=(3, 11))


def _test_zero_relevance_gate() -> None:
    from src.search_service import SearchService, SearchResponse, SearchResult

    zero = SearchResult(
        title="Generic market filler",
        snippet="No direct relation",
        url="https://example.com/a",
        source="example",
        relevance_score=0,
        relevance_category=SearchService._SECTOR_NEWS_CATEGORY,
    )
    filtered = SearchService._filter_ranked_news_for_context(
        SearchResponse(query="TEST", results=[zero], provider="test", success=True),
        log_scope="preflight",
    )
    assert filtered.results == [], "zero-relevance filler must be dropped"

    direct = SearchResult(
        title="Direct instrument evidence",
        snippet="Direct security mention",
        url="https://example.com/b",
        source="example",
        relevance_score=0,
        relevance_category=SearchService._DIRECT_INSTRUMENT_NEWS_CATEGORY,
    )
    filtered_direct = SearchService._filter_ranked_news_for_context(
        SearchResponse(query="TEST", results=[direct], provider="test", success=True),
        log_scope="preflight",
    )
    assert len(filtered_direct.results) == 1


def _test_evidence_gate() -> None:
    from src.analyzer import GeminiAnalyzer

    evidence = """
[E01] date=2026-08-08 | source=Reuters | scope=fresh | category=direct_company_news | relevance=100
[E02] date=2026-05-01 | source=Research | scope=analytical | category=direct_company_news | relevance=90
""".strip()

    result = SimpleNamespace(
        dashboard={
            "intelligence": {
                "latest_news": "2026-08-08 verified event [E01]",
                "risk_alerts": [
                    "2026-08-08 unsupported event",
                    "2026-08-08 analytical-only event [E02]",
                    "2026-08-08 verified risk [E01]",
                ],
                "positive_catalysts": ["generic catalyst without evidence"],
            }
        }
    )
    summary = GeminiAnalyzer._enforce_news_evidence_gate(result, evidence, code="TEST")
    assert summary["removed"] == 3
    assert result.dashboard["intelligence"]["risk_alerts"] == [
        "2026-08-08 verified risk [E01]"
    ]
    assert result.dashboard["intelligence"]["positive_catalysts"] == [
        "暂无已验证的近期证据"
    ]


def _test_json_mode_classifier() -> None:
    from src.analyzer import GeminiAnalyzer

    previous = os.environ.get("LITELLM_JSON_OBJECT_MODE")
    try:
        os.environ["LITELLM_JSON_OBJECT_MODE"] = "true"
        assert GeminiAnalyzer._supports_json_object_mode("deepseek/deepseek-v4-flash")
        assert GeminiAnalyzer._supports_json_object_mode("deepseek/deepseek-v4-pro")
        assert not GeminiAnalyzer._supports_json_object_mode("openai/gpt-5.6")
        assert GeminiAnalyzer._is_response_format_parameter_error(
            ValueError("response_format is unsupported")
        )
        assert not GeminiAnalyzer._is_response_format_parameter_error(
            ValueError("connection timeout")
        )
        os.environ["LITELLM_JSON_OBJECT_MODE"] = "false"
        assert not GeminiAnalyzer._supports_json_object_mode("deepseek/deepseek-v4-flash")
    finally:
        if previous is None:
            os.environ.pop("LITELLM_JSON_OBJECT_MODE", None)
        else:
            os.environ["LITELLM_JSON_OBJECT_MODE"] = previous



def _test_preaccept_forecast_and_evidence_contract() -> None:
    from src.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    # Keep this preflight network/config independent: validation failures are
    # represented by a plain ValueError instead of resolving runtime backends.
    analyzer._generation_validation_error = (
        lambda *args, **kwargs: ValueError(kwargs.get("reason", "validation"))
    )
    evidence = (
        "[E01] date=2026-08-08 | source=Reuters | scope=fresh | "
        "category=direct_company_news | relevance=100\n"
        "[E02] date=2026-05-01 | source=Research | scope=analytical | "
        "category=direct_company_news | relevance=90"
    )
    data = {
        "forecast": {
            "horizons": {
                "5d": {
                    "direction": "bullish",
                    "up_probability": 60,
                    "expected_return_pct": 1.0,
                },
                "10d": {
                    "direction": "neutral",
                    "up_probability": 52,
                    "expected_return_pct": None,
                },
                "20d": {
                    "direction": "bearish",
                    "up_probability": 45,
                    "expected_return_pct": -1.0,
                },
            }
        },
        "dashboard": {
            "intelligence": {
                "latest_news": "2026-08-08 verified [E01]",
                "risk_alerts": ["暂无已验证的近期证据"],
                "positive_catalysts": ["2026-08-08 catalyst [E01]"],
            }
        },
    }
    analyzer._validate_forecast_contract(data)
    analyzer._validate_raw_news_evidence_contract(data, evidence)

    bad_date = {
        **data,
        "dashboard": {
            "intelligence": {
                "latest_news": "2026-08-07 wrong-date evidence [E01]",
                "risk_alerts": [],
                "positive_catalysts": [],
            }
        },
    }
    try:
        analyzer._validate_raw_news_evidence_contract(bad_date, evidence)
    except Exception:
        pass
    else:
        raise AssertionError("mismatched Evidence date must be rejected")


def _test_quote_reuse_cache() -> None:
    from data_provider.base import DataFetcherManager

    previous = os.environ.get("REALTIME_QUOTE_REUSE_TTL_SECONDS")
    os.environ["REALTIME_QUOTE_REUSE_TTL_SECONDS"] = "30"
    try:
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._ensure_concurrency_guards()
        quote = SimpleNamespace(price=123.45)
        manager._store_reused_realtime_quote("MSFT", quote)
        assert manager._get_reused_realtime_quote("MSFT") is quote
    finally:
        if previous is None:
            os.environ.pop("REALTIME_QUOTE_REUSE_TTL_SECONDS", None)
        else:
            os.environ["REALTIME_QUOTE_REUSE_TTL_SECONDS"] = previous


def _test_relative_strength_states() -> None:
    from data_provider.market_regime_adapter import MarketRegimeAdapter

    fn = MarketRegimeAdapter._relative_strength_state
    assert fn(1.0, 2.0) == "outperform"
    assert fn(-1.0, -2.0) == "underperform"
    assert fn(1.0, -1.0) == "mixed"
    assert fn(None, 1.0) == "unknown"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    _compile_critical_files(root)

    import src.analyzer  # noqa: F401
    import src.search_service  # noqa: F401
    import src.core.pipeline  # noqa: F401
    import data_provider.base  # noqa: F401
    import data_provider.market_regime_adapter  # noqa: F401

    _test_zero_relevance_gate()
    _test_evidence_gate()
    _test_json_mode_classifier()
    _test_preaccept_forecast_and_evidence_contract()
    _test_quote_reuse_cache()
    _test_relative_strength_states()
    print("accuracy preflight: PASS")


if __name__ == "__main__":
    main()
