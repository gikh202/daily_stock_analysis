# V6 最终日报质量与结构化输出恢复

本文档记录 V6 统一日报在最终邮件展示、V4 研究融合和结构化 LLM 恢复上的稳定性边界。改动只收敛输入质量、展示和失败恢复，不新增第二套投资决策逻辑；最终动作、是否值得买入和执行授权仍以 `FinalDecisionPacket` 为唯一权威来源。

## V4 研究融合

- `analysis_history` 中 `code=MARKET` 的大盘复盘记录不属于个股，不进入 V4 per-symbol research view，也不计入融合标的数量。
- “暂无已验证的近期证据”“暂无已验证的近期新闻证据”等中性占位文本在进入最终证据融合前会被过滤；真实、带日期和 Evidence ID 的催化/风险证据继续保留。
- 该清洗只影响最终报告可见证据，不修改 V4/V6 机会分、风险分、预测方向或 `FinalDecisionPacket` 决策规则。

## 最终邮件表格

V6 最终邮件在发送前对 Markdown 生成的 HTML `<table>` 增加内联 `display:table !important` 与稳定的边框/布局声明，用于避免部分邮件客户端把表格错误压成一列。表头单元格仍保持独立，例如：

```html
<th>标的</th>
<th>动作</th>
<th>主预测</th>
<th>量化方向</th>
<th style="text-align:right;">机会</th>
<th style="text-align:right;">风险</th>
```

该处理只作用于 V6 final unified email，不改变报告 Markdown、JSON artifact、其他通知渠道或决策内容。

## LiteLLM 结构化恢复

结构化分析继续使用调用方原有 validator 作为最终准入条件，并采用以下顺序：

1. 主模型/备用模型按现有路由生成结构化结果。
2. 如果所有已配置模型都返回了内容但未通过 validator，仅当原始响应已经包含**结构闭合的 JSON 根对象/数组**时，才允许本地 `json_repair` 修复尾逗号、轻微格式漂移等语法问题。
3. 本地修复结果必须再次通过同一个原始 validator；validator 不会被弱化或替换。
4. 缺失闭合括号、未闭合字符串等疑似截断响应禁止本地自动补全，即使外层 validator 很宽松，也必须进入一次 evidence-aware 模型修复。
5. evidence-aware 修复失败时保留原来的失败/确定性 post-gate 语义，不用新的传输错误覆盖原始可诊断异常。

因此，本地 repair 只用于降低可安全修复的 JSON 语法漂移和额外模型调用，不会把被 token 截断的不完整分析伪装成成功结果。

## 验证与回滚

相关回归覆盖：

- MARKET 记录不会进入个股融合。
- 中性近期证据占位不会污染最终报告证据。
- 邮件表头允许携带对齐 style，但必须保持独立 `<th>` 单元格。
- 闭合但带尾逗号的 JSON 可本地修复。
- 未闭合的截断 JSON 即使 validator 宽松，也必须触发模型修复。

回滚时可整体回退对应提交；不需要数据库迁移，也不改变 V6 normalized 表、Stage 13 Gate 或 `FinalDecisionPacket` schema。
