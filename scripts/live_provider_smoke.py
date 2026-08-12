#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small real-network smoke checks for providers used by stock analysis.

This script is intentionally separate from PR CI. It validates live third-party
contracts on a schedule so provider/API drift is visible without making a
transient external outage a code-merge gate.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional


_SECRET_ENV_NAMES = (
    "SERPAPI_API_KEY",
    "SERPAPI_API_KEYS",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEYS",
    "OPENAI_API_KEY",
    "OPENAI_API_KEYS",
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEYS",
    "AIHUBMIX_KEY",
    "LITELLM_API_KEY",
    "LLM_PRIMARY_API_KEY",
    "LLM_PRIMARY_API_KEYS",
    "LLM_SECONDARY_API_KEY",
    "LLM_SECONDARY_API_KEYS",
)
_LLM_CONFIG_ENV_NAMES = (
    "AGENT_LITELLM_MODEL",
    "LITELLM_MODEL",
    "LLM_CHANNELS",
    "LITELLM_CONFIG",
    "LITELLM_CONFIG_YAML",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEYS",
    "OPENAI_API_KEY",
    "OPENAI_API_KEYS",
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEYS",
    "AIHUBMIX_KEY",
    "LLM_PRIMARY_API_KEY",
    "LLM_PRIMARY_API_KEYS",
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: str
    required: bool
    latency_ms: int
    detail: str


def _split_secret_values(*values: Optional[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        text = str(raw or "").replace("\r", "\n")
        for line in text.split("\n"):
            for item in line.split(","):
                value = item.strip()
                if value and value not in out:
                    out.append(value)
    return out


def _redact(value: object) -> str:
    text = str(value or "")
    secrets: list[str] = []
    for name in _SECRET_ENV_NAMES:
        secrets.extend(_split_secret_values(os.getenv(name)))
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, "***")
    return " ".join(text.split())[:1000]


def _run_probe(name: str, *, required: bool, probe: Callable[[], str]) -> ProbeResult:
    started = time.monotonic()
    try:
        detail = probe()
        status = "pass"
    except Exception as exc:  # noqa: BLE001 - smoke runner must summarize every provider failure.
        detail = f"{type(exc).__name__}: {exc}"
        status = "fail"
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    return ProbeResult(
        name=name,
        status=status,
        required=required,
        latency_ms=latency_ms,
        detail=_redact(detail),
    )


def _skipped(name: str, detail: str) -> ProbeResult:
    return ProbeResult(
        name=name,
        status="skipped",
        required=False,
        latency_ms=0,
        detail=detail,
    )


def _probe_yfinance() -> str:
    from data_provider.yfinance_fetcher import YfinanceFetcher

    symbol = str(os.getenv("LIVE_SMOKE_SYMBOL", "AAPL") or "AAPL").strip().upper()
    frame = YfinanceFetcher().get_daily_data(symbol, days=5)
    if frame is None or frame.empty:
        raise RuntimeError(f"Yahoo Finance returned no rows for {symbol}")
    required_columns = {"date", "close"}
    missing = required_columns.difference(frame.columns)
    if missing:
        raise RuntimeError(f"Yahoo Finance normalized frame missing columns: {sorted(missing)}")
    latest_close = float(frame.iloc[-1]["close"])
    if latest_close <= 0:
        raise RuntimeError(f"Yahoo Finance returned invalid latest close: {latest_close}")
    return f"symbol={symbol} rows={len(frame)} latest_close={latest_close:.4f}"


def _serpapi_keys() -> list[str]:
    return _split_secret_values(
        os.getenv("SERPAPI_API_KEYS"),
        os.getenv("SERPAPI_API_KEY"),
    )


def _probe_serpapi(keys: list[str]) -> str:
    from src.search_service import SearchService

    service = SearchService(
        serpapi_keys=keys,
        searxng_base_urls=[],
        searxng_public_instances_enabled=False,
        news_max_age_days=7,
        news_strategy_profile="short",
    )
    if not service.is_available:
        raise RuntimeError("SerpAPI was configured but SearchService reports unavailable")

    response = service.search_topic_news(
        "Apple AAPL",
        max_results=2,
        focus_keywords=["AAPL Apple stock latest news"],
    )
    if not response.success:
        raise RuntimeError(response.error_message or "SerpAPI search failed")
    return f"provider={response.provider} parsed_results={len(response.results or [])}"


def _flag_enabled(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name, "true" if default else "false") or "").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _llm_configuration_present() -> bool:
    return any(str(os.getenv(name, "") or "").strip() for name in _LLM_CONFIG_ENV_NAMES)


def _probe_llm() -> str:
    from src.agent.llm_adapter import LLMToolAdapter

    adapter = LLMToolAdapter()
    if not adapter.is_available:
        raise RuntimeError("LLM configuration is present but Agent LiteLLM route is unavailable")

    response = adapter.call_text(
        [
            {"role": "system", "content": "Provider health probe. Reply with a short acknowledgement."},
            {"role": "user", "content": "Reply with exactly OK."},
        ],
        temperature=0,
        max_tokens=8,
        timeout=20,
    )
    if response.provider == "error":
        raise RuntimeError(response.content or "LLM adapter returned provider=error")
    content = str(response.content or "").strip()
    if not content:
        raise RuntimeError("LLM adapter returned an empty text response")
    return f"provider={response.provider or 'unknown'} model={response.model or 'unknown'} response={content[:40]}"


def _required_failures(results: list[ProbeResult]) -> list[ProbeResult]:
    return [item for item in results if item.required and item.status == "fail"]


def run_smoke() -> list[ProbeResult]:
    results = [
        _run_probe("yfinance_daily", required=True, probe=_probe_yfinance),
    ]

    serpapi_keys = _serpapi_keys()
    if serpapi_keys:
        results.append(
            _run_probe(
                "serpapi_search",
                required=True,
                probe=lambda: _probe_serpapi(serpapi_keys),
            )
        )
    else:
        results.append(_skipped("serpapi_search", "SERPAPI_API_KEY(S) not configured"))

    llm_enabled = _flag_enabled("LIVE_SMOKE_LLM_ENABLED", default=True)
    if llm_enabled and _llm_configuration_present():
        results.append(_run_probe("llm_text", required=True, probe=_probe_llm))
    elif not llm_enabled:
        results.append(_skipped("llm_text", "LIVE_SMOKE_LLM_ENABLED=false"))
    else:
        results.append(_skipped("llm_text", "No LLM route/API configuration detected"))

    return results


def _write_report(results: list[ProbeResult]) -> Path:
    target = Path(os.getenv("LIVE_SMOKE_REPORT", "artifacts/live-provider-smoke.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": not _required_failures(results),
        "results": [asdict(item) for item in results],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _append_github_summary(results: list[ProbeResult]) -> None:
    summary_path = str(os.getenv("GITHUB_STEP_SUMMARY", "") or "").strip()
    if not summary_path:
        return
    lines = [
        "## Live Provider Smoke",
        "",
        "| Provider | Status | Required | Latency | Detail |",
        "|---|---|---:|---:|---|",
    ]
    for item in results:
        detail = item.detail.replace("|", "\\|")
        lines.append(
            f"| {item.name} | {item.status} | {'yes' if item.required else 'no'} | "
            f"{item.latency_ms} ms | {detail} |"
        )
    lines.append("")
    lines.append(
        "Result: **FAIL**" if _required_failures(results) else "Result: **PASS**"
    )
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    results = run_smoke()
    report_path = _write_report(results)
    _append_github_summary(results)

    for item in results:
        print(
            f"[{item.status.upper()}] {item.name} required={item.required} "
            f"latency_ms={item.latency_ms} detail={item.detail}"
        )
    print(f"Report: {report_path}")

    failures = _required_failures(results)
    if failures:
        print("Required live provider smoke checks failed: " + ", ".join(item.name for item in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
