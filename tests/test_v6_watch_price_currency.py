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
    assert (
        _normalize_watch_price_yuan("若股价跌破并收于110元下方")
        == "若股价跌破并收于$110下方"
    )
    assert (
        _normalize_watch_price_yuan("若股价跌破并且收于110元下方")
        == "若股价跌破并且收于$110下方"
    )
    assert (
        _normalize_watch_price_yuan("若股价走弱但公司获得99元补贴则关注基本面")
        == "若股价走弱但公司获得99元补贴则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若股价表现改善同时公司支付99元罚款则关注基本面")
        == "若股价表现改善同时公司支付99元罚款则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若股价表现改善！公司支付99元罚款则关注基本面")
        == "若股价表现改善！公司支付99元罚款则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若股价表现改善?公司支付99元罚款则关注基本面")
        == "若股价表现改善?公司支付99元罚款则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若股价表现改善同时关注中国售价99元的销量反馈")
        == "若股价表现改善同时关注中国售价99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan("若产品售价接近99元则观察销量")
        == "若产品售价接近99元则观察销量"
    )
    assert (
        _normalize_watch_price_yuan("若公司补贴达到99元则关注基本面")
        == "若公司补贴达到99元则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若产品单价达到99元则观察销量")
        == "若产品单价达到99元则观察销量"
    )
    assert (
        _normalize_watch_price_yuan("若客单价接近99元则观察销量")
        == "若客单价接近99元则观察销量"
    )
    assert (
        _normalize_watch_price_yuan("若公司赔偿达到99元则关注基本面")
        == "若公司赔偿达到99元则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若公司罚款达到99元则止损")
        == "若公司罚款达到99元则止损"
    )
    assert (
        _normalize_watch_price_yuan("若客户买入99元套餐则关注订阅转化")
        == "若客户买入99元套餐则关注订阅转化"
    )
    assert (
        _normalize_watch_price_yuan("若产品成本跌破99元则关注毛利")
        == "若产品成本跌破99元则关注毛利"
    )
    assert (
        _normalize_watch_price_yuan("若营收突破99元则关注基本面")
        == "若营收突破99元则关注基本面"
    )
    assert (
        _normalize_watch_price_yuan("若产品成本恶化后股价跌破99元则止损")
        == "若产品成本恶化后股价跌破$99则止损"
    )
    assert (
        _normalize_watch_price_yuan("关注产品价格99元的销量反馈")
        == "关注产品价格99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan("关注服务价格99元的客户反馈")
        == "关注服务价格99元的客户反馈"
    )
    assert (
        _normalize_watch_price_yuan("关注商品价格99元的销量反馈")
        == "关注商品价格99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan("关注零售价格99元的销量反馈")
        == "关注零售价格99元的销量反馈"
    )
    assert (
        _normalize_watch_price_yuan("关注订阅价格99元的续费反馈")
        == "关注订阅价格99元的续费反馈"
    )
    assert _normalize_watch_price_yuan("若价格跌破110元则止损") == "若价格跌破$110则止损"
    assert _normalize_watch_price_yuan("若股价接近99元则止损") == "若股价接近$99则止损"
    assert _normalize_watch_price_yuan("若跌至110元则止损") == "若跌至$110则止损"
    assert _normalize_watch_price_yuan("若触及110元则减仓") == "若触及$110则减仓"
    assert _normalize_watch_price_yuan("跌破 110 元止损") == "跌破 $110止损"
    assert _normalize_watch_price_yuan("股价回踩120-121 元分批") == "股价回踩$120-121分批"


def test_negated_chase_span_stops_at_clause_connectors() -> None:
    assert (
        _rewrite_affirmative_chase_clauses("不宜现在买入但突破后可以追涨")
        == "不宜现在买入但突破后仅视为强势确认，不追价"
    )
    assert (
        _rewrite_affirmative_chase_clauses("不宜现在买入！突破后可以追涨")
        == "不宜现在买入！突破后仅视为强势确认，不追价"
    )
    assert (
        _rewrite_affirmative_chase_clauses("不宜现在买入?突破后可以追涨")
        == "不宜现在买入?突破后仅视为强势确认，不追价"
    )
    assert (
        _rewrite_affirmative_chase_clauses("突破后可以追！")
        == "突破后仅视为强势确认，不追价！"
    )
    assert (
        _rewrite_affirmative_chase_clauses("突破后可以追?")
        == "突破后仅视为强势确认，不追价?"
    )
    assert (
        _rewrite_affirmative_chase_clauses("不宜仅因短线反弹追买")
        == "不宜仅因短线反弹追买"
    )


def test_no_chase_guard_uses_current_execution_or_risk_fields_only() -> None:
    revoked = """### 1. TEST · Example · 最终：观察

- **投研摘要**：此前禁止追高限制已解除
- **下一次确认条件**：
  - 突破后可以追涨
"""
    revoked_card = _standardize_stock_card(revoked)
    assert "突破后可以追涨" in revoked_card
    assert "突破后仅视为强势确认，不追价" not in revoked_card

    active = """### 1. TEST · Example · 最终：观察

- **主要风险**：
  - 不宜仅因短线反弹追买
- **下一次确认条件**：
  - 突破后可以追！
"""
    active_card = _standardize_stock_card(active)
    assert "突破后仅视为强势确认，不追价！" in active_card
    assert "突破后可以追！" not in active_card


def test_auxiliary_risk_control_price_uses_usd_without_touching_cny_facts() -> None:
    section = """### 1. TEST · Example · 最终：等待

- **交易计划**：
  - **风险控制**: 止损参考 466.00 元；若中国售价99元的产品销量恶化则减仓；仓位不超过3%
"""
    email_card = _standardize_stock_card(section)

    assert (
        "**辅助风险控制（非执行）**: 止损参考 $466.00；若中国售价99元的产品销量恶化则减仓；仓位不超过3%"
        in email_card
    )
    assert "466.00 元" not in email_card
    assert "中国售价$99" not in email_card
