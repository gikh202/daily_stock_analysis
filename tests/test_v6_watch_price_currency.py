from __future__ import annotations

from src.v6_daily.accuracy_report import (
    _normalize_watch_price_yuan,
    _rewrite_affirmative_chase_clauses,
    _standardize_stock_card,
)


def test_watch_price_yuan_normalization_is_amount_scoped() -> None:
    assert (
        _normalize_watch_price_yuan(
            "若股价跌破355.00元，关注中国售价99元的销量反馈"
        )
        == "若股价跌破$355.00，关注中国售价99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan(
            "若股价突破200美元，关注中国售价99元的销量反馈"
        )
        == "若股价突破200美元，关注中国售价99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan(
            "若股价突破200美元后关注中国售价99元的销量反馈"
        )
        == "若股价突破200美元后关注中国售价99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan(
            "若股价突破200美元后回踩180元"
        )
        == "若股价突破200美元后回踩$180"
    )
    assert (
        _normalize_watch_price_yuan(
            "关注中国售价99元的销量反馈，同时股价回踩180-182元"
        )
        == "关注中国售价99元的销量反馈，同时股价回踩$180-182"
    )


def test_negated_chase_span_stops_at_clause_connectors() -> None:
    assert (
        _rewrite_affirmative_chase_clauses("不宜现在买入但突破后可以追涨")
        == "不宜现在买入但突破后仅视为强势确认，不追价"
    )
    assert (
        _rewrite_affirmative_chase_clauses("不宜仅因短线反弹追买")
        == "不宜仅因短线反弹追买"
    )


def test_auxiliary_risk_control_price_uses_usd() -> None:
    section = """### 1. TEST · Example · 最终：等待

- **交易计划**：
  - **风险控制**: 止损参考466.00元；仓位不超过3%
"""
    email_card = _standardize_stock_card(section)

    assert "**辅助风险控制（非执行）**: 止损参考$466.00；仓位不超过3%" in email_card
    assert "466.00元" not in email_card
