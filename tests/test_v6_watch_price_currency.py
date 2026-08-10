from __future__ import annotations

from src.v6_daily.accuracy_report import _normalize_watch_price_yuan


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
            "关注中国售价99元的销量反馈，同时股价回踩180-182元"
        )
        == "关注中国售价99元的销量反馈，同时股价回踩$180-182"
    )
