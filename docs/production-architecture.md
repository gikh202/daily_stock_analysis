# 当前生产架构与运行契约

> 本文描述 `gikh202/daily_stock_analysis` 当前生产分支的实际运行方式。上游 README 中的通用项目介绍仍保留多市场兼容背景；涉及本 Fork 的美股 V4/V6、开盘确认、最终邮件和回测门禁时，以本文与 workflow 本身为准。

## 1. 决策链

生产最终结论不是由单次 LLM 文本直接产生：

1. `00-daily-analysis.yml` 运行 V4 主分析，生成结构化分析记录、上下文快照、报告和 SQLite Artifact。
2. V6 从同一轮 V4 Artifact 的结构化数据构造确定性特征与多周期预测。
3. `AlphaDecisionEngine` 只对结构化数值证据评分；LLM 文本不直接参与 V6 数值评分。
4. `FinalDecisionPacket` 融合 V4 研究方向和 V6 确定性计划，分别给出 `worth_buying` 与 `execution_authorized`。
5. `03-v6-daily.yml` 通过 Production Gate 后才允许发送最终综合日报。

任何 V4 缺失、最终契约不完整、数据质量保护触发或 Production Gate 失败，都不得把上游研究文本冒充成最终可执行指令。

## 2. 生产调度

### 收盘/日常主分析

`00-daily-analysis.yml` 当前定时为：

```text
30 22 * * 1-5
```

即 UTC 22:30。工作流内部仍会执行交易日和数据完整性检查。

### 美股开盘后确认

`01-us-open-confirmation.yml` 的目标检查点是纽约时间 **09:45 ET（常规开盘后 15 分钟）**。由于 GitHub Actions `schedule` 不是实时调度器，主工作流同时配置多个 EDT/EST 候选点，并再次使用 `America/New_York` 本地时间门控。

主链路显式执行：

```text
V4 workflow_dispatch
  -> 等待成功
  -> 取得准确 V4 run id
  -> V6 workflow_dispatch(upstream_run_id=<V4 run id>)
  -> 等待最终 V6/通知成功
```

### 调度 watchdog

`01-us-open-schedule-watchdog.yml` 使用独立 UTC 候选点覆盖纽约 09:45–12:30 补偿窗口。它只把最近自动化 V6 最终报告的成功/进行中状态视为覆盖证据；主确认 workflow 自身的 `success` 不能证明邮件已发送，因为窗口外 gate skip 也可能是 success。

PR 会运行 `scripts/validate_us_open_reliability.py`，同时回测 EDT 与 EST 映射及最大补偿间隔。

## 3. Provider 生产契约

`02-live-provider-smoke.yml` 与 PR 离线 CI 分离。它检查真实第三方契约：

- Yahoo Finance 日线：始终 required。
- SerpAPI：仓库配置 Key 时 required。
- LLM：检测到生产路由配置且未禁用时 required。

Reasoning 模型的 smoke 输出预算由 `LIVE_SMOKE_LLM_MAX_TOKENS` 控制，默认 512，最低 64，避免 reasoning token 用尽后出现“健康模型被误判空文本”。

## 4. 依赖可复现性

`requirements.txt` 继续表达兼容范围；GitHub Actions 生产/关键回测安装必须同时使用：

```bash
pip install -c constraints-production.txt -r requirements.txt
```

`constraints-production.txt` 是经过 CI/Provider 验证的直接依赖快照。升级关键数据源、LiteLLM/OpenAI、pandas/numpy、FastAPI 等依赖时，应在独立 PR 更新 constraints，并重新运行完整 CI、Provider smoke（可手动）和 V6 回测。

## 5. 回测与策略门禁

`ci-v6-architecture-backtest.yml` 使用同一份 3 年研究数据库对 PR head/base 做 no-lookahead 回放，当前覆盖 MSFT、GOOGL、QQQM、VOO，并包含 SPY/QQQ benchmark。

新增 `scripts/assert_v6_strategy_quality.py` 后，回测不仅检查“代码输出等价”，还检查：

- 至少存在成熟的独立 Alpha 样本；
- 成熟 Champion 的方向命中率不得低于 50%；
- 标的选择相对 SPY 的平均超额必须为正；
- signed Alpha Target 的命中率不得低于 50%，平均 Alpha return 必须为正；
- PR 不得对 Champion 命中、标的相对 SPY 超额和 Alpha return 造成超过容忍度的退化；
- Challenger 若要成为晋级候选，必须满足独立样本、正 Alpha、正 SPY 选择超额、方向改善，并要求 Alpha 命中率 95% CI 下界高于 50%。

注意：`avg_excess_vs_spy_pct` 是带 bullish/bearish/neutral 暴露的方向策略与长期持有 SPY 的比较，二者 beta 暴露不同，因此不作为绝对晋级条件。生产门禁使用更可比的 `avg_underlying_excess_vs_spy_pct` 与 signed Alpha Target 指标。

## 6. 架构复杂度预算

现有历史代码中仍存在大型模块。为避免继续扩大单体，在渐进式拆分完成前，`scripts/check_architecture_budget.py` 对最大的核心文件设置字节预算。任何继续膨胀都会先在 CI 失败；后续重构应把数据获取、上下文构建、决策追踪、通知编排等职责逐步移入服务模块，并同步下调预算。

## 7. 最终邮件语义

最终投资邮件必须以 `FinalDecisionPacket` 为业务真相源：

- `worth_buying` 表示跨层研究结论是否具有可买价值；
- `execution_authorized` 表示当前是否真的允许执行；
- 当前可执行仓位与条件触发后的未来仓位上限必须分开呈现；
- V4 的 `买入/观望/减仓/卖出` 只能标记为上游投研动作，不能覆盖最终执行动作；
- Markdown 仅负责展示，不能从文本重新推导业务决策。
