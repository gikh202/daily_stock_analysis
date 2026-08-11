# 美股双时点分析与邮件推送

本项目在 GitHub Actions 模式下使用两次互补分析，而不是用一个固定 UTC 时间同时承担盘中确认和盘后复盘。

## 默认时点

| 时点 | Workflow | 触发语义 |
| --- | --- | --- |
| 美股开盘 15 分钟后 | `.github/workflows/01-us-open-confirmation.yml` | 通过 13:45 UTC / 14:45 UTC 两个候选 cron，再按 `America/New_York` 当前 UTC offset 二次校验，只在 09:45 ET 真正 dispatch 完整分析 |
| 美股收盘后 | `.github/workflows/00-daily-analysis.yml` | 工作日 22:30 UTC 运行完整分析；该时间在 EDT/EST 下均晚于美股常规交易时段收盘 |

开盘确认 workflow 不复制分析逻辑，而是 dispatch `00-daily-analysis.yml` 的完整模式。因此两次运行共用同一套：

- 股票列表与市场阶段识别
- 数据源和 LLM / Agent 配置
- 报告生成与 DecisionSignal 风控
- 邮件、Telegram、企业微信等既有通知渠道
- 交易日检查与报告 artifact

这样可以避免两套 workflow 长期产生配置漂移。

## 邮件配置

要让两个时点都发送邮件，在 Repository Settings 中至少配置：

```text
EMAIL_SENDER
EMAIL_PASSWORD
EMAIL_RECEIVERS
```

可选：

```text
EMAIL_SENDER_NAME
```

`EMAIL_PASSWORD` 应使用邮箱提供商要求的 SMTP 授权码 / App Password，而不是在不支持普通密码登录的邮箱服务中直接填写账户登录密码。

两个 workflow 都复用 `00-daily-analysis.yml` 的通知环境，因此不需要为开盘确认再配置一套邮箱凭据。

## 休市与 DST

- `01-us-open-confirmation.yml` 使用 `America/New_York` offset gate 处理 EDT / EST，避免夏令时切换后固定 UTC cron 偏移一小时。
- 工作日 cron 只是候选触发。美国市场节假日仍由项目现有交易日检查决定是否实际分析；不要仅凭 `Mon-Fri` cron 判断当天一定开市。
- 如需验证，可手动运行 `01-us-open-confirmation.yml` 的 `workflow_dispatch`，以及 `00-daily-analysis.yml` 的手动入口。

## 避免重复任务

不要再新增第三个“美股收盘”或“09:45 ET” workflow。重复 schedule 会造成：

- 同一股票重复请求行情、新闻和 LLM
- 重复写入分析历史 / DecisionSignal
- 重复发送邮件
- 增加第三方 API 配额与模型费用

如需改变执行时间，应修改现有两个 workflow，并同步更新其 DST / trading-calendar 回归验证，而不是额外复制一份工作流。
