from types import SimpleNamespace

from src.v6_daily.final_decision_renderer import _normalize_report_presentation
from src.v6_daily.fusion_contracts import FinalVerdict, FusionAgreement


def _packet(symbol: str, v4_operation: str = "观望"):
    return SimpleNamespace(
        symbol=symbol,
        effective_trade_date="2026-08-12",
        v4_operation=v4_operation,
        agreement=FusionAgreement.ALIGNED,
        non_trading=False,
        assessment=SimpleNamespace(
            verdict=FinalVerdict.WAIT,
            worth_buying=False,
            execution_authorized=False,
            bullish_evidence=(),
            bearish_evidence=(),
            key_boundaries=(),
        ),
        execution=SimpleNamespace(has_active_plan=False, max_position_pct=0.0),
        v4_horizon="10d",
        v4_expected_return_pct=None,
        v4_direction="neutral",
        v6_direction="neutral",
        opportunity_score=None,
        risk_score=None,
    )


def test_normalization_preserves_boundaries_between_multiple_stock_cards() -> None:
    report = (
        "# AI 美股综合日报 · 2026-08-13\n\n"
        "### 1. MSFT · Microsoft · 最终：观察\n"
        "- **是否值得买**：**暂不买，等待确认**\n"
        "- **当前执行授权**：**否**\n"
        "- **当前执行授权**：**否**\n"
        "- **当前可执行仓位上限**：**0.0%**\n"
        "- **预测层 vs 执行层**：一致；当前执行 **观望**\n"
        "### 2. VOO · Vanguard S&P 500 ETF · 最终：观察\n"
        "- **是否值得买**：**暂不买，等待确认**\n"
        "- **预测层 vs 执行层**：一致；当前执行：**观望**\n"
        "### 3. GOOGL · Alphabet · 最终：观察\n"
        "- **是否值得买**：**暂不买，等待确认**\n"
        "- **预测层 vs 执行层**：一致；当前执行:**观望**\n"
        "## 4. 大模型与数据健康度\n"
    )

    normalized = _normalize_report_presentation(
        report,
        [_packet("MSFT"), _packet("VOO"), _packet("GOOGL")],
    )

    assert "\n### 2. VOO" in normalized
    assert "\n### 3. GOOGL" in normalized
    assert "\n## 4. 大模型与数据健康度" in normalized
    assert "MSFT · Microsoft · 最终：观察" in normalized
    assert "VOO · Vanguard S&P 500 ETF · 最终：观察" in normalized
    assert "GOOGL · Alphabet · 最终：观察" in normalized
    assert "当前执行 **" not in normalized
    assert "当前执行：**" not in normalized
    assert "当前执行:**" not in normalized
    assert normalized.count("上游投研动作 **观望**（非最终执行）") == 3
    assert normalized.count("- **是否值得买**：**暂不买，等待确认**") == 3
    assert normalized.count("- **当前执行授权**：**否**") == 3
    assert normalized.count("- **当前可执行仓位上限**：**0.0%**") == 3
