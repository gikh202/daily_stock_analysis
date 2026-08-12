from __future__ import annotations

from src.v6_daily.final_decision_renderer import (
    _normalize_next_check_presentation,
    _normalize_us_price_units,
)


def test_us_price_units_replace_bare_yuan_leaks_in_us_stock_cards() -> None:
    source = (
        "- **关键分界**：观察开盘后是否站稳MA5(708.92元)；"
        "若放量突破300元整数关口则确认\n"
        "- **基本面**：最新财季营收900.07亿美元，净利润357.66亿美元"
    )

    normalized = _normalize_us_price_units(source)

    assert "MA5($708.92)" in normalized
    assert "$300整数关口" in normalized
    assert "708.92元" not in normalized
    assert "300元" not in normalized
    assert "900.07亿美元" in normalized
    assert "357.66亿美元" in normalized


def test_next_check_is_canonical_open_plus_15_checkpoint() -> None:
    variants = (
        "  - 下次检查：**2026-08-12 09:30 开盘后**",
        "  - 下次检查：**2026-08-12 09:30 ET（美东）（开盘后30分钟）**",
        "  - 下次检查：**2026-08-12T09:45:00-04:00**",
        "  - 下次检查：**2026-08-12 09:30 ET (美股开盘)**",
    )

    for source in variants:
        normalized = _normalize_next_check_presentation(source)
        assert normalized == "  - 下次检查：**2026-08-12 09:45 ET（开盘后15分钟）**"


def test_next_check_without_explicit_date_is_left_unchanged() -> None:
    source = "  - 下次检查：开盘后等待确认"

    assert _normalize_next_check_presentation(source) == source
