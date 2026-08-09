# Change Log

All notable changes to this project will be documented in this file.

## [Unreleased]

- [新功能] V6.2 Accuracy Lab 新增非重叠样本与 Wilson 95% 置信区间、BUY_SETUP 保守 OHLC 执行回测、版本化 execution policy、Champion/Challenger shadow、严格 no-lookahead 历史回放和每周准确率研究工作流；生产 Champion 不自动调参或晋级。

- [新功能] 集成 LiteLLM，支持通过 LiteLLM 统一调用多种大模型 API
  - 新增 `LITELLM_API_KEY`、`LITELLM_BASE_URL`、`LITELLM_MODEL` 环境变量
  - 支持模型别名与 fallback 模型列表配置
  - 保留原有 Gemini、DeepSeek、OpenAI、Anthropic 等直连方式
  - 详见 [docs/litellm.md](litellm.md)
- [新功能] 接入 MiniMax 搜索 API 作为新闻搜索源
  - 新增 `MINIMAX_API_KEYS` 环境变量
  - 支持与其他搜索引擎自动 fallback
- [新功能] 新增 LongPort / Longbridge 行情数据源
  - 支持 OAuth 和 Legacy API Key 两种鉴权方式
  - 新增 `LONGBRIDGE_OAUTH_CLIENT_ID`、`LONGBRIDGE_OAUTH_CLIENT_SECRET`、`LONGBRIDGE_OAUTH_TOKEN_CACHE_B64` 环境变量
  - 支持美股、港股、A 股实时行情
- [新功能] 新增 V5 Alpha Engine 实验层
  - 确定性 Quality / Opportunity / Risk 评分
  - 缺失证据降低置信度，不再用伪造中性值代替
  - 硬风险否决、仓位上限和最低 1.5R 交易计划门槛
  - 与 V4 生产分析隔离运行，不自动修改生产建议
- [新功能] V5 Alpha Validation
  - 增加方向命中率、Profit Factor、Spearman Opportunity IC、Sharpe / Drawdown proxy、Confidence Calibration 等确定性验证指标
  - 增加只读历史回放，严格使用分析日之后的未来 K 线进行验证
  - 增加研究 Gate，不达标时不会影响 V4 生产逻辑
- [新功能] V6 Daily Intelligence
  - 在 V4 生产分析之后增加独立 V6 确定性决策层
  - 使用历史 V4 结构化快照，不重新构造历史 Prompt
  - Forecast / Opportunity / Quality / Risk / 仓位不受 LLM 自由文本直接影响
  - 5/10/20 个未来交易日自动成熟结果并维护准确率 Scoreboard
  - AVOID 作为“回避”单独验证，不再误当作做空
  - 支持 SEC EDGAR / FRED 免费公开数据作为证据层
  - 默认最小研究样本门槛提高至 50
- [新功能] V6.0.1 单封中文综合日报
  - V4 继续生成完整报告/SQLite/Artifact，但在 GitHub Actions 中不再单独发送日报邮件
  - V6 workflow 自动下载同一次 V4 workflow run 的报告 Artifact，并生成唯一的最终中文综合邮件
  - V6 人类可读字段、市场脉搏、机会排名、交易计划与验证看板统一中文化
  - 手动 V6 无 V4 Artifact 时自动降级为仅发送 V6 中文日报
- [新功能] V6.0.2 V4 + V6 语义融合日报
  - 不再把 V4 Markdown 整段拼接到 V6 后面，而是从 `analysis_history.raw_result` 读取结构化 V4 投研结果
  - 每个标的融合 V4 预测/新闻/财报/技术面/基本面/量价/作战计划与 V6 Forecast/Opportunity/Quality/Risk/交易计划
  - 明确输出“方向一致 / 部分一致 / 方向分歧”，冲突时按风险优先只降级、不升级行动等级
  - V4 模型上行概率明确标记为“未校准”，避免误当成真实胜率
- [新功能] V6.1 Accuracy Foundation
  - 5D / 10D / 20D 使用独立确定性权重预测，避免一个方向跨三个周期验证
  - STOCK / ETF 分离评分，VOO/QQQM 等 ETF 不套用个股基本面/财报权重
  - 同标的+有效交易日+引擎版本去重，同日重复运行不增加独立验证样本
  - SEC CompanyFacts/XBRL 增加营收、利润、现金流、FCF、债务、稀释等确定性基本面特征与覆盖门槛
  - FRED 增加利率/信用/VIX level + change 宏观风险特征，当前快照禁止回填历史记录避免 look-ahead
  - 仅来源可追溯的结构化 Catalyst 进入数值评分，LLM 自由文本保持 0 数值影响
  - Risk Engine 2.0 增加波动、事件、Gap、趋势破位、宏观、数据风险
  - 5/10/20D Outcome 分别持久化对应 forecast/direction，并增加 SPY/QQQ excess return、Regime、经验校准桶
  - 新增严格 no-lookahead 历史 replay CLI 与 Accuracy Foundation 测试
- [优化] V6 最终邮件改为股票投资者视图
  - 最终 Gmail 仅保留市场判断、今日动作、5D/10D/20D 预测、逐标的投研/量化融合、催化、风险和交易计划
  - LLM 健康、SQLite、运行健康、SEC/FRED 原始诊断等技术信息保留在 Artifact，不再占用邮件正文
  - 预测样本不足时不展示空验证表；达到成熟样本后自动显示方向命中率
- [优化] DeepSeek / LiteLLM 结构化输出可靠性
  - validator-backed 结构化请求温度上限降至 0.2，降低 JSON / enum / Evidence 漂移
  - 所有模型返回内容但验证失败时允许一次 <=0.1 的 Evidence-aware 定向修复，禁止编造日期/Evidence ID
  - 修复回合强制 non-stream，并增强 `schema_validation_failed` 的安全原因日志
- [修复] V6 自动链路一致性与最终 Gate
  - V6 `workflow_run` 只接受 main 的成功 V4，checkout 精确绑定上游 `head_sha`
  - 自动 V6 直接使用同一次 V4 Artifact 中的 `stock_analysis.db`，避免最近 Cache 与本轮报告错配
  - 生成阶段不再提前发送邮件；先验证 SQLite/JSON/中文内容/V4 融合，再保存研究历史并发送最终邮件
  - 去除绑定固定 Markdown 标题的“是否中文”错误判定，改为元数据+中文字符+结构+标的一致性校验
  - V6 独立研究历史改为串行不中断，失败诊断 Artifact 保持上传

## [2.4.0] - 2026-05-11

### Added
- Added `docs/full-guide.md` as the single complete configuration and operations guide for GitHub Actions, local runs, Docker, Web, desktop, notification channels, LLMs, search providers, stock data sources, troubleshooting, and production validation.
- Added `docs/quickstart.md` as a minimal working path for first-time GitHub Actions users.
- Added `scripts/render_markdown_mermaid.py` to pre-render supported Mermaid fences into SVGs for GitHub Pages without requiring the browser to execute Mermaid itself.
- Added a checked-in placeholder favicon at `docs/assets/favicon.svg` and a zero-config `favicon_path` fallback in `mkdocs.yml`.

### Changed
- Reorganized `README.md` as a layered index instead of a second long-form manual, with direct paths to quickstart, the full guide, release downloads, deployment methods, and advanced scenarios.
- Rewrote `docs/system-architecture.md` so the checked-in source is useful as plain Markdown and remains compatible with the existing Mermaid pre-render workflow.
- Updated `mkdocs.yml` navigation, social links, favicon metadata, copyright, and explicitly disabled MathJax injection by default.
- Improved the GitHub Pages workflow so Mermaid rendering, MkDocs build, and deployment are visible as separate steps, with a built-site verification step before upload.
- Updated `.env.example` to document `MINIMAX_API_KEYS` and `LITELLM_CONFIG`, and cleaned related inline comments.
- Synced `docs/DEVELOPMENT.md`, `docs/system-architecture.md`, and `docs/full-guide.md` with the supported Python 3.10+ baseline.

### Fixed
- Removed stale references to `docs/notification-guide.md` and `docs/configuration.md` from the main user-facing paths.
- Replaced the obsolete Gemini token-guide URL with the official Google AI Studio API-key URL.
- Documented and made explicit that WhatsApp / wxpusher support is optional and not provided by this repository by default.

### Verified
- Verified the unified full guide against the current code paths for GitHub Actions, Docker, Web, desktop, notifications, LLMs, search providers, and stock data-source behavior.
- Verified the cleaned environment template with `python scripts/env_audit.py --env-file .env.example`.
- Verified the Mermaid pre-render step and strict MkDocs production build.

## [2.3.1] - 2026-05-10

### Added
- Added native LongPort / Longbridge quote support, including OAuth-based and legacy API-key authentication.
- Added multi-recipient email configuration and routing support.
- Added notification routing, severity filters, quiet hours, cooldowns, deduplication, digest mode, and grouped delivery policy controls.
- Added Web health, Web env validation, AI readiness, Web runtime configuration, and diagnostics endpoints.
- Added a reusable GitHub dependency-review workflow and Dependabot configuration.

### Changed
- Changed the default web bind behavior to support container deployment and updated related documentation.
- Updated Web / Docker / desktop GitHub Actions to current action majors.
- Updated the default GitHub Actions checkout/setup/upload/download/cache action majors.
- Consolidated duplicate Web runtime/config endpoints and aligned the frontend environment contract with backend configuration.
- Hardened Web Docker defaults and removed unsafe example secrets from generated compose configuration.

### Fixed
- Fixed duplicate route definitions and inconsistent frontend/backend runtime environment naming.
- Fixed a malformed Docker startup command and related container readiness issues.

## [2.3.0] - 2026-05-09

### Added
- Added Google Gemini 3 support and provider fallback improvements.
- Added Brave Search and MiniMax search support.
- Added DingTalk Stream mode support.
- Added Feishu bot improvements.
- Added AstrBot notification support.
- Added Slack notifications.
- Added customized email titles and unified HTML/Markdown rendering.
- Added pre-market analysis support and more flexible watchlist handling.
- Added optional LongPort / Longbridge data support.
- Added Web and desktop management applications.

### Changed
- Improved technical-indicator handling and reporting.
- Improved Docker and Web deployment flows.
- Updated documentation and configuration examples for the expanded provider set.

### Fixed
- Fixed multiple CI, packaging, and documentation consistency issues.
