# US Market Two-Checkpoint Analysis and Email Delivery

GitHub Actions uses two complementary analysis checkpoints instead of forcing intraday confirmation and post-close review into one fixed UTC schedule.

## Default checkpoints

| Checkpoint | Workflow | Trigger semantics |
| --- | --- | --- |
| 15 minutes after the US open | `.github/workflows/01-us-open-confirmation.yml` | Targets 09:45 ET. To tolerate GitHub Actions scheduling delays, lightweight compensation candidates also run at 09:55, 10:10, 10:25, 10:40, 10:55, and 11:10 ET. The orchestrator reuses the successful/in-flight V4/V6 chain so the candidates do not intentionally send duplicate final emails. |
| After the US close | `.github/workflows/00-daily-analysis.yml` | Runs the full analysis at 22:30 UTC on weekdays. GitHub scheduled jobs may queue and start later, so the actual email can arrive after the nominal 06:30 Taipei/Beijing time. |

The open-confirmation workflow dispatches the existing full daily-analysis workflow rather than duplicating analysis logic. Both checkpoints therefore share the same stock list, market-phase handling, data/LLM configuration, report and DecisionSignal guardrails, notification channels, trading-day checks, and report artifacts.

## Email configuration

To receive email from both checkpoints, configure at least these Repository Settings values:

```text
EMAIL_SENDER
EMAIL_PASSWORD
EMAIL_RECEIVERS
```

Optional:

```text
EMAIL_SENDER_NAME
```

Use the SMTP authorization code / app password required by your mail provider when ordinary account passwords are not accepted.

Both checkpoints reuse the notification environment from `00-daily-analysis.yml`; no second set of email credentials is required for the open-confirmation run.

## Decision and email consistency

The final email, analysis history, and automatic DecisionSignal use the same finalized action. When the AnalysisContextPack explicitly reports `poor` core-evidence quality, an otherwise actionable `buy/add` is downgraded to `watch` before public output and persistence, with the data-quality guardrail reason retained for auditability. This prevents the email from recommending a buy while the stored signal has already been downgraded.

Insufficient technical history is represented as unavailable rather than inferred from placeholder values. MA60 is exposed only when enough real history exists, and technical-score coverage records which indicator groups are actually available before an active buy conclusion can be produced.

## Market holidays, DST, and scheduling delay

- `01-us-open-confirmation.yml` uses the `America/New_York` timezone directly, so daylight-saving transitions do not shift the target by an hour.
- The multiple open candidates are compensation triggers, not an instruction to send multiple emails. A successful or in-flight V4/V6 chain is reused by later scheduled candidates.
- Weekday cron expressions are only candidate triggers. Existing trading-day checks still decide whether analysis should actually run on US market holidays.
- GitHub Actions scheduling is not a hard real-time scheduler and may run late when the platform queue is busy; the 22:30 UTC post-close schedule can be delayed as well.
- For validation, use the manual `workflow_dispatch` entry on the open-confirmation workflow and the manual entry on the daily-analysis workflow.

## Avoid duplicate schedules

Do not add a third workflow for the same post-close or 09:45 ET checkpoint. Duplicate schedules would duplicate market/news/LLM requests, history and DecisionSignal writes, email delivery, and external API/model usage.

If the timing needs to change, update the existing workflows and their DST/trading-calendar regression validation instead of copying the workflow.
