from __future__ import annotations

from src.v6_daily.final_decision_renderer import _normalize_next_check_presentation


def test_next_check_normalization_preserves_section_trailing_newlines() -> None:
    source = (
        "### 1. MSFT · Microsoft · 最终：观察 · 方向一致\n"
        "- **下次检查**：2026-08-12 13:00（美东）或收盘前\n"
        "\n"
    )

    normalized = _normalize_next_check_presentation(source)

    assert normalized.endswith("\n\n")
    assert "2026-08-12 09:45 ET（开盘后15分钟）" in normalized


def test_next_check_normalization_does_not_glue_adjacent_heading() -> None:
    first = (
        "### 1. MSFT · Microsoft · 最终：观察 · 方向一致\n"
        "- **下次检查**：2026-08-12 13:00（美东）或收盘前\n"
    )
    second = "### 2. VOO · Vanguard S&P 500 ETF · 最终：观察 · 方向一致\n"

    normalized = _normalize_next_check_presentation(first) + second

    assert "\n### 2. VOO" in normalized
    assert "分钟）**### 2. VOO" not in normalized
