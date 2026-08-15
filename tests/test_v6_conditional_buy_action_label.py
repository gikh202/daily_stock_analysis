from types import SimpleNamespace

from src.v6_daily.fusion_contracts import FinalVerdict, action_label_zh


def test_conditional_buy_action_label_is_actionable() -> None:
    packet = SimpleNamespace(
        assessment=SimpleNamespace(
            verdict=FinalVerdict.CONDITIONAL_BUY,
            worth_buying=True,
        ),
        non_trading=False,
    )

    assert action_label_zh(packet) == "等待触发后买入"
