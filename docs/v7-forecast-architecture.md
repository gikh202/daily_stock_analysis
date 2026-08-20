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

Challenger 只有在 forward-only 样本达到最低门槛，且 Brier Score 相比 Champion 有明确改善时才允许晋级。晋级后，active probability、历史收益分布、校准样本数和 forecast confidence 都切换到 Challenger 对应校准结果，而不是只替换一个概率数字。

## 5. Decision Layer

Forecast Decision Policy 使用：

- 校准后的多周期上涨概率；
- 期望 Return / Alpha；
- P10 downside 与 MAE；
- 分布化 Reward/Risk；
- deterministic risk score；
- forecast confidence。

风险硬门仍优先于方向预测。Decision Layer 只产生 BUY_SETUP / WATCH / WAIT / AVOID 和最大风险仓位；Trade Plan 再根据 support / resistance / ATR 构造确定性入场区间、止损和目标位，并继续执行最低 R:R 约束。

## 6. Intraday Timing Layer

开盘后不再把收盘计划当成“是否现在买”的机械答案。盘中择时先执行原有 quote freshness、计划日期、止损、风险否决等硬校验，然后结合：

- 当前相对 session range 位置；
- VWAP 溢价/折价；
- 最近 5 分钟动量；
- 当前盘中波动；
- 1D / 5D 上涨概率；
- 当前价格相对风险入场区间的位置；

估计 `better_entry_probability`、预计改善幅度和参考更优价。

最终状态：

- `BUY_NOW`：当前执行的期望值高于继续等待；
- `WAIT_BETTER_ENTRY`：标的仍值得买，但更可能出现更好的近端入场价格；
- `WAIT_CONFIRMATION`：价格可能已经便宜，但弱势尚未结束；
- `NO_BUY` / `INVALIDATED`：硬否决/计划失效；
- `DATA_UNAVAILABLE`：暂不能判断，保持后续复查资格。

## 7. Outcome Learning Loop

现有 normalized forecast/outcome 数据库继续保存 future return、MFE、MAE、benchmark return 和 excess alpha。V7 的 horizon payload 额外保存 Champion/Challenger 概率，所以后续 outcome 成熟后可以直接重新计算校准误差和模型表现，不需要 LLM 参与反馈学习。

生产 outcome horizon 已扩展为 1D / 5D / 10D / 20D，使盘中择时使用的 1D 概率也可以形成真实 forward-only 校准样本，而不是长期停留在先验状态。

学习闭环遵循：

`Forecast -> Mature Outcome -> Calibration / Model Metrics -> Shadow Challenger -> Promotion Gate -> Future Forecast`

任何历史训练或回放都必须保持 strict no-lookahead；当前 SEC/FRED 等外部快照也不得回填到历史预测中。

## 8. 兼容性

现阶段保留 `V6DailyEngine`、`v6_daily_latest.json`、`FinalDecisionPacket v1` 和既有 Artifact 名称，作为外部接口兼容层；其内部生产 Forecast Engine 已升级到 `v7.0-forecast.1`。这样 GitHub Actions、审计存储和现有报告消费者可以渐进迁移，而不是一次破坏所有下游接口。
