# V7 Forecast / Decision Architecture

V7 把“预测”和“交易执行”拆成独立、可验证的层，避免继续依赖单一 0–100 人工评分直接决定 BUY/WATCH/AVOID。

## 1. 数据与证据层

结构化行情、趋势、成交量、相对强弱、财务、事件、宏观与市场 Regime 进入统一特征层。LLM 可以整理来源明确的新闻/事件，但自然语言正文不直接参与数值概率、仓位或止损计算。

缺失证据保持缺失并降低 evidence coverage，不填充伪造的中性值。

## 2. Forecast Layer

生产预测输出 1D / 5D / 10D / 20D：

- `probability_up`：经历史结果校准后的上涨概率；
- `expected_return_pct`：期望收益；
- `expected_alpha_vs_spy_pct`：相对 SPY 的期望 Alpha；
- `p10 / p50 / p90`：未来收益分布；
- `expected_mfe_pct / expected_mae_pct`：预期有利/不利路径；
- `forecast_confidence`：综合证据覆盖、历史校准样本和模型一致性的预测可信度；
- `evidence_coverage`：数据是否齐全，与上涨概率严格分离。

结构化上游预测的原始收益数值会尽量保留到 Forecast 层，不再过早全部压缩成 0–100 分。

## 3. Calibration Layer

`ForecastHistory` 只读取已经成熟且严格早于当前 effective trade date 的 outcome：

- outcome 的 `end_trade_date` 必须早于本次预测日期；
- 原预测日期也必须早于本次预测日期；
- 当前/未来结果绝不允许参与当前样本的概率校准；
- effective trade date 缺失或非法时直接使用 `prior_only`，不读取任何历史 outcome，也不使用未来哨兵日期。

校准统计包含：

- Brier Score；
- Log Loss；
- Expected Calibration Error (ECE)；
- 历史平均 Return / Alpha / MFE / MAE；
- 历史 P10 / P50 / P90。

Regime 样本足够时优先使用同 Regime 历史；否则退回 horizon 级历史。样本不足时采用 Bayesian shrinkage 向当前模型先验收缩，并标记 `prior_only` / `shrunk`，不制造虚假的高置信度。

生产运行不新增独立的 V7 历史库环境变量。`V7ForecastEngine` 默认读取标准 `v6_data/v6_daily.db`，并允许调用方显式注入 history database；这样不会出现一个隐藏的 `V7_FORECAST_HISTORY_DB` 与生产 normalized forecast/outcome 数据库长期漂移。测试和研究回放可以直接注入隔离的 `ForecastHistory`。

## 4. Regime + Champion / Challenger

V7 默认 Champion 是 `calibrated_ensemble`，同时记录 `momentum_challenger` 的 shadow 概率。

Champion 与 Challenger 必须分别使用自己的历史概率序列进行分桶和 Brier/Log Loss 统计。旧 V6 历史可以用于 Champion 冷启动校准，但由于历史里没有 Challenger 概率，不能伪装成 Challenger 样本。

Challenger 只有在 **Champion 与 Challenger 同时存在原生预测的 paired forward-only 样本**达到最低门槛，且 Brier Score 相比 Champion 有明确改善时才允许晋级；旧 V6 Champion-only 样本不得进入 Promotion 比赛。每个 1D / 5D / 10D / 20D horizon 独立选择 Champion，5D 晋级不能自动接管其他周期。晋级后，active probability、历史收益分布、校准样本数和 forecast confidence 都切换到对应 horizon 的 Challenger 校准结果，而不是只替换一个概率数字。

## 5. Decision Layer

Forecast Decision Policy 使用：

- 校准后的多周期上涨概率；
- 期望 Return / Alpha；
- P10 downside 与 MAE；
- 分布化 Reward/Risk；
- deterministic risk score；
- forecast confidence。

风险硬门仍优先于方向预测。`prior_only`、预测可信度不足、非正 Alpha 或分布 R:R 不足都属于真正的执行门：存在这些 gate 时只能 WAIT/零新仓，不能仅把 gate 写进诊断后继续生成 BUY_SETUP/WATCH 仓位。Decision Layer 只产生 BUY_SETUP / WATCH / WAIT / AVOID 和最大风险仓位；Trade Plan 再根据 support / resistance / ATR 构造确定性入场区间、止损和目标位，并继续执行最低 R:R 约束。

## 6. Intraday Timing Layer

开盘后不再把收盘计划当成“是否现在买”的机械答案。盘中择时先执行原有 quote freshness、计划日期、止损、风险否决等硬校验，然后结合：

- 当前相对 session range 位置；
- VWAP 溢价/折价；
- 最近 5 分钟动量；
- 当前盘中波动；
- 1D / 5D 上涨概率；
- 当前价格相对风险入场区间的位置；

当前盘中公式输出 `better_entry_score`（兼容保留旧字段 `better_entry_probability`），它是 **未校准的启发式评分**，同时给出预计改善幅度和参考更优价；在 Research Ledger 积累并验证足够多时点 outcome 之前，不得把该 score 表述为历史胜率或校准概率。

最终状态：

- `BUY_NOW`：当前执行的期望值高于继续等待；
- `WAIT_BETTER_ENTRY`：标的仍值得买，但更可能出现更好的近端入场价格；
- `WAIT_CONFIRMATION`：价格可能已经便宜，但弱势尚未结束；
- `NO_BUY` / `INVALIDATED`：硬否决/计划失效；
- `DATA_UNAVAILABLE`：暂不能判断，保持后续复查资格。

## 7. Outcome Learning Loop

现有 normalized forecast/outcome 数据库继续保存 future return、MFE、MAE、benchmark return 和 excess alpha。V7 的 horizon payload 额外保存 Champion/Challenger 概率，所以后续 outcome 成熟后可以直接重新计算校准误差和模型表现，不需要 LLM 参与反馈学习。

生产 outcome horizon 已扩展为 1D / 5D / 10D / 20D，使盘中择时使用的 1D 概率也可以形成真实 forward-only 校准样本，而不是长期停留在先验状态。盘中 Research Ledger v2 以 `session_date + symbol + policy + source_run + signal_bar_time` 记录同一标的一天内的多次新鲜评估，并在次日结算 `WAIT_BETTER_ENTRY` 的参考更优价是否真正触达、最大可改善幅度和触达时间；这条 outcome 链路只用于验证/未来校准，不把启发式 score 预先包装成概率。

学习闭环遵循：

`Forecast -> Mature Outcome -> Calibration / Model Metrics -> Shadow Challenger -> Promotion Gate -> Future Forecast`

任何历史训练或回放都必须保持 strict no-lookahead；当前 SEC/FRED 等外部快照也不得回填到历史预测中。

## 8. 兼容性

现阶段保留 `V6DailyEngine`、`v6_daily_latest.json`、`FinalDecisionPacket v1` 和既有 Artifact 名称，作为外部接口兼容层；其内部生产 Forecast Engine 已升级到 `v7.1-forecast.1`。这样 GitHub Actions、审计存储和现有报告消费者可以渐进迁移，而不是一次破坏所有下游接口。

## 9. 合并门禁

V7 进入 `main` 前必须以最终 PR head 通过 V6 Daily CI、V6 Architecture Backtest、美股开盘专项回测和通用 CI；早期 head 的成功结果不能替代最终代码的验证。用户可见的预测、盘中择时和邮件行为同时记录在 `docs/CHANGELOG.md` 的 `[Unreleased]` 中。
