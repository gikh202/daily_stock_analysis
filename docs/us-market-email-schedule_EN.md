# US Market Two-Checkpoint Analysis and Email Delivery

GitHub Actions uses two complementary analysis checkpoints instead of forcing intraday confirmation and post-close review into one fixed UTC schedule.

## Default checkpoints

| Checkpoint | Workflow | Trigger semantics |
| --- | --- | --- |
| 15 minutes after the US open | `.github/workflows/01-us-open-confirmation.yml` | Uses 13:45 UTC / 14:45 UTC candidate cron entries, then validates the current `America/New_York` UTC offset so only the true 09:45 ET candidate dispatches the full analysis |
| After the US close | `.github/workflows/00-daily-analysis.yml` | Runs the full analysis at 22:30 UTC on weekdays; this remains after the regular US session close under both EDT and EST |

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

## Market holidays and DST

- `01-us-open-confirmation.yml` uses an `America/New_York` offset gate to cover EDT / EST without a one-hour drift at daylight-saving transitions.
- Weekday cron expressions are only candidate triggers. Existing trading-day checks still decide whether analysis should actually run on US market holidays.
- For validation, use the manual `workflow_dispatch` entry on the open-confirmation workflow and the manual entry on the daily-analysis workflow.

## Avoid duplicate schedules

Do not add a third workflow for the same post-close or 09:45 ET checkpoint. Duplicate schedules would duplicate market/news/LLM requests, history and DecisionSignal writes, email delivery, and external API/model usage.

If the timing needs to change, update the existing workflows and their DST/trading-calendar regression validation instead of copying the workflow.
