from __future__ import annotations

from src.notification_sender.email_sender import EmailSender, _default_report_subject
from src.v6_daily.accuracy_report import (
    build_investor_email_markdown,
    extract_investor_email_subject,
)


class _EmailConfig:
    email_sender = "sender@gmail.com"
    email_sender_name = "Daily Stock"
    email_password = "app-password"
    email_receivers = ["receiver@example.com"]
    stock_email_groups = []


def _full_report() -> str:
    return """# AI 美股综合日报 · 2026-08-09

> 本报告不是 V4 与 V6 的原文拼接：V4 提供 AI 投研；V6 提供确定性评分。

## 1. 今日最终总览

- 市场状态：**风险偏好开启**
- 市场广度：**广泛**
- V6 平均机会分 / 风险分：**68.0 / 41.0**
- 平均证据覆盖率：**72%**
- **综合判断**：市场风险偏好与广度共同偏多，环境对多头更友好。

### 最终优先级

| 排名 | 标的 | 最终动作 | 融合状态 | V4预测 | V6方向 | V6预测分 | 机会分 | 风险分 | 证据 |
|---:|---|---|---|---|---|---:|---:|---:|---:|
| 1 | MSFT | 观察 | 方向一致 | 10d 看多 | 看多 | 78.0 | 72.0 | 38.0 | 80% |
| 2 | QQQM | 观察 | 部分一致 | 10d 看多 | 中性 | 58.0 | 61.0 | 43.0 | 75% |

## 2. 今日变化

- 本轮没有达到 5 分变化阈值的显著变化，或这是该标的首次 V6 记录。

## V6.1 多周期确定性预测

> 5D、10D、20D 使用不同的确定性权重；10D 仍作为兼容主预测。分数不是胜率，真实概率只会在历史样本达到门槛后校准。

| 标的 | 类型 | 5D | 10D | 20D | 机会分 | 风险分 |
|---|---|---|---|---|---:|---:|
| MSFT | 个股 | 看多 72.0（证据80%） | 看多 78.0（证据80%） | 看多 81.0（证据75%） | 72.0 | 38.0 |
| GOOGL | 个股 | 中性 49.0（证据80%） | 中性 56.0（证据80%） | 看多 65.0（证据75%） | 59.0 | 45.0 |

### FRED 宏观风险

- 宏观风险分：**42.0**
- 10Y-2Y 利差：**0.300**

### SEC CompanyFacts 基本面

- **MSFT**：质量分 **88.0**，营收同比 **12.0%**。

## 3. 标的融合分析

### 1. MSFT · Microsoft Corporation · 最终：观察 · 方向一致

- **最终结论**：**观察**。V4 10d预测看多，V6确定性方向看多，机会分72.0、风险分38.0，两层方向一致。 V4执行护栏：等待盘中确认，严禁追高。
- **V4 投研摘要**：云业务保持增长，趋势偏多但等待价格确认（证据来自公司公告）。新闻证据可追溯。
- **V6 确定性视角**：方向 **看多** | 预测分 **78.0** | 机会/质量/风险 **72.0/84.0/38.0** | 证据 **80%**
- **V6 因子**：趋势 82 | 动量 70 | 相对强弱 76 | 量能 64 | 基本面 88 | 市场状态 80
- **预测层 vs 执行层**：10d **看多** | 预期收益 **+5.0%** | 模型上行概率 **65%（未校准）** | 当前执行 **观望**
- **核心驱动/催化**：
  - 云业务增速保持强劲。
- **技术面**：均线多头排列。
- **量价确认**：后市需放量突破500亿级别成交额确认启动新一轮攻势。
- **主要风险**：
  - 高位波动扩大。
- **融合交易计划**：
  - **融合入场区间**: `[495.0, 500.0]`（优先采用 确定性风控计划）
  - **止损/失效位**: `480.0`
  - **目标位**: `[525.0, 540.0]`
  - **V6 最大仓位上限**: `10%`
  - **V4 价格参考**: 理想买入点：496.00元；止损位：466.00元；目标位：510.00元
  - **V4 仓位参考**: 小仓/低仓位
  - **V4 风险控制**: 止损参考466.00元；仓位不超过3%
- **下一次确认条件**：
  - 若放量上涨突破515，日内可追但仅限小仓位
  - 若高开超过3%，突破后不可追高，回踩企稳后可以追涨
  - 回踩后可以追涨但不可追高
  - 若价格跌破498.50元则继续观望
  - 下次检查：**2026-08-10 09:30 EDT**
- **数据限制**：催化因子尚未进入数值评分

### 2. GOOGL · Alphabet Inc. · 最终：等待 · 部分一致

- **最终结论**：**等待**。10d预测看多，但V6确定性方向中性，尚未形成完全共振。 V4执行护栏：暂不追高，接近压力时不得追买。
- **V4 投研摘要**：中长期趋势仍在，等待确定性风控计划确认后再判断短中期机会；新品在中国售价99元（证据不足）。
- **V6 确定性视角**：方向 **中性** | 预测分 **56.0** | 机会/质量/风险 **59.0/70.0/45.0** | 证据 **77%**
- **融合交易计划**：
  - **参考入场**: 理想买入点：361.29元（回踩MA5）
  - **V4 价格参考**: 区间买入180-182元
  - **止损/失效位**: 止损位：348.85元（跌破MA20）
  - **目标位**: 目标位：370.00元
  - **V4 仓位参考**: 小仓/低仓位
- **下一次确认条件**：
  - 价格能否守住354.01元
  - 若站上365可以追涨
  - 高价股展示样例1,234.56元必须保持完整数字
  - 下次检查：**2026-08-10 10:00 EST**

## 4. 大模型与数据健康度

- V4/上游 LLM 状态：正常 1
- V4 结构化投研记录参与融合：**2** 个标的

## 5. 宏观与官方数据

### 宏观快照
- VIX: **14.9**

### 近期 SEC 文件
- **MSFT**: 10-Q 2026-07-30

## 6. 预测验证看板

- 状态：**数据不足**
- 每个周期最小样本门槛：**50**

| 周期 | 样本数 | 方向命中率 | 买入样本 | 买入命中率 | 回避样本 | 回避命中率 | 预测 IC | 机会 IC |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5D | 0 | N/A | 0 | N/A | 0 | N/A | N/A | N/A |

## 7. 运行健康度

- 新增 V6 信号：**2**
- SQLite 完整性检查：**ok**

## 方法说明

- V4 负责新闻；V6 负责评分。

*生成器：V6 融合报告层 · v6.1*
"""


def test_investor_email_keeps_one_canonical_execution_view() -> None:
    email = build_investor_email_markdown(_full_report())

    assert "# 美股决策日报 · 2026-08-09" in email
    assert "### 今日动作" in email
    assert "| MSFT | 观察 | 10d 看多 | 看多 | 72.0 | 38.0 |" in email
    assert "## 多周期预测" in email
    assert "因子覆盖80%" in email
    assert "分数与因子覆盖率用于相对比较" in email
    assert "（证据来自公司公告）" in email
    assert "新闻证据可追溯" in email
    assert "新闻证据仅视为强势确认" not in email
    assert "（因子覆盖来自公司公告）" not in email
    assert "## 标的详解" in email
    assert "执行优先级：今日动作优先" in email
    assert "仅当确定性交易计划具有正数最大仓位上限时" in email
    assert "组合风控将最大仓位压至 0 时，保留价位不可执行" in email
    assert "投研摘要" in email
    assert "量化视角" in email
    assert "总体证据覆盖 **80%**" in email
    assert "关键因子" in email
    assert "交易计划" in email
    assert "确定性风控计划为唯一执行价格口径" in email
    assert email.count("确定性风控计划为唯一执行价格口径") == 1
    assert "**融合入场区间**: `[495.0, 500.0]`（唯一执行价格口径）" in email
    assert "最大仓位上限" in email

    assert "模型上行概率" not in email
    assert "理想买入点：$496.00" not in email
    assert "止损位：$466.00" not in email
    assert "目标位：$510.00" not in email
    assert "止损参考$466.00" not in email
    assert "仓位不超过3%" not in email
    assert "**价格参考**" not in email
    assert "**风险控制**" not in email
    assert "500亿级别成交额" not in email
    assert "日内可追" not in email
    assert "仅视为强势确认，不追价" in email
    assert "突破后不可追高" in email
    assert "回踩企稳后可以追涨" not in email
    assert "回踩企稳后仅视为强势确认，不追价" in email
    assert "回踩后可以追涨但不可追高" not in email
    assert "回踩后仅视为强势确认，不追价但不可追高" in email
    assert "V4执行护栏" not in email
    assert "执行护栏：等待盘中确认，严禁追高" in email
    assert "$498.50" in email
    assert "$1,234.56" in email
    assert "1,$234.56" not in email
    assert "ET（美东）" in email
    assert " EDT" not in email
    assert " EST" not in email

    assert "## 2. 今日变化" not in email
    assert "FRED" not in email
    assert "SEC" not in email
    assert "大模型与数据健康度" not in email
    assert "运行健康度" not in email
    assert "SQLite" not in email
    assert "生成器：" not in email
    assert "数值评分" not in email
    assert "V4 " not in email
    assert "V6 " not in email


def test_investor_email_marks_fallback_plan_as_non_execution() -> None:
    email = build_investor_email_markdown(_full_report())

    assert "等待确定性风控计划确认后再判断短中期机会" in email
    assert "新品在中国售价99元（证据不足）" in email
    assert "新品在中国售价$99" not in email
    assert "因子覆盖不足" not in email
    assert "执行护栏：暂不追高，接近压力时不得追买" in email
    assert "若站上365可以追涨" not in email
    assert "若站上365仅视为强势确认，不追价" in email
    assert "**辅助交易计划（未触发）**" in email
    assert "**辅助入场参考（非执行）**: 理想买入点：$361.29" in email
    assert "**辅助价格参考（非执行）**: 区间买入$180-182" in email
    assert "180-$182" not in email
    assert "**辅助仓位参考（非执行）**: 小仓/低仓位" in email
    assert "止损位：$348.85" in email
    assert "目标位：$370.00" in email
    assert "价格能否守住$354.01" in email


def test_investor_email_recognizes_descriptive_no_chase_guard() -> None:
    report = _full_report().replace(
        "暂不追高，接近压力时不得追买。",
        "不宜仅因短线反弹追买。",
    )
    email = build_investor_email_markdown(report)

    assert "执行护栏：不宜仅因短线反弹追买" in email
    assert "若站上365可以追涨" not in email
    assert "若站上365仅视为强势确认，不追价" in email


def test_investor_email_treats_rounded_positive_position_marker_as_active() -> None:
    report = _full_report().replace(
        "  - **V6 最大仓位上限**: `10%`\n",
        "  - **V6 最大仓位上限**: `0.0%`\n",
    )
    email = build_investor_email_markdown(report)

    assert "**融合入场区间**: `[495.0, 500.0]`（唯一执行价格口径）" in email
    assert "确定性风控计划为唯一执行价格口径" in email
    assert "**交易计划（当前不可执行）**" not in email
    assert "组合风控当前禁止新仓" not in email


def test_investor_email_marks_preserved_zero_position_plan_inactive() -> None:
    report = _full_report().replace(
        "  - **V6 最大仓位上限**: `10%`\n",
        "",
    )
    email = build_investor_email_markdown(report)

    assert "**交易计划（当前不可执行）**" in email
    assert "**保留入场区间（当前不可执行）**: `[495.0, 500.0]`（组合风控当前禁止新仓）" in email
    assert "确定性风控计划为唯一执行价格口径" not in email
    assert "（唯一执行价格口径）" not in email
    assert "**辅助价格参考（非执行）**" in email
    assert "**辅助风险控制（非执行）**" in email


def test_investor_email_subject_uses_top_stock_actions(monkeypatch) -> None:
    email = build_investor_email_markdown(_full_report())
    assert extract_investor_email_subject(email) == "美股决策日报｜MSFT 观察 · QQQM 观察｜08-09"

    monkeypatch.setenv("V6_UNIFIED_EMAIL_FINAL", "true")
    assert _default_report_subject(email) == "美股决策日报｜MSFT 观察 · QQQM 观察｜08-09"


def test_upstream_unified_email_suppression_is_success_not_failure(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("MERGE_EMAIL_NOTIFICATION", "true")
    monkeypatch.delenv("V6_UNIFIED_EMAIL_FINAL", raising=False)

    sender = EmailSender(_EmailConfig())
    assert sender._is_email_configured() is False
    assert sender.send_to_email("# upstream report") is True