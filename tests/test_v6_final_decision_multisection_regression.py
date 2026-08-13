from types import SimpleNamespace

from src.v6_daily.final_decision_renderer import _normalize_report_presentation


def _packet(symbol: str, v4_operation: str = "观望"):
    return SimpleNamespace(
        symbol=symbol,
        effective_trade_date="2026-08-12",
        v4_operation=v4_operation,
        assessment=SimpleNamespace(execution_authorized=False),
        execution=SimpleNamespace(has_active_plan=False, max_position_pct=0.0),
    )


def test_normalization_preserves_boundaries_between_multiple_stock_cards() -> None:
    report = (
        "# AI 美股综合日报 · 2026-08-13\n\n"
        "### 1. MSFT · Microsoft · 最终：观察\n"
        "- **是否值得买**：**暂不买，等待确认**\n"
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
