# US Market Low-Latency Email Schedule

The production schedule separates **time-critical execution email** from the heavier V4/V6/V7 research chain. GitHub Actions remains a best-effort scheduler, so UTC cron entries are candidate triggers and runtime gates use `America/New_York` plus the XNYS calendar.

## Default checkpoints

| Checkpoint | Workflow | Production behavior |
| --- | --- | --- |
| US open | `.github/workflows/01-us-open-confirmation.yml` | Starts at 09:30 ET with dense 09:30–09:35 compensation candidates. A single fresh 1-minute bar is enough for a conservative first decision; later candidates add more opening evidence. It installs only `requirements-realtime.txt` and sends email through the lightweight SMTP path. |
| US close flash | `.github/workflows/00a-us-close-flash.yml` | Candidate triggers at 20:00 and 21:00 UTC cover EDT/EST. The New York runtime gate keeps only the real 16:00 ET window, validates the XNYS session, reuses the most recent successful V6/V7 plan, and sends a low-latency close snapshot before deep analysis. |
| Deep post-close analysis | `.github/workflows/00-daily-analysis.yml` → `.github/workflows/03-v6-daily.yml` | Candidate triggers at 20:05 and 21:05 UTC. The New York runtime gate keeps only the true post-close run. V4 performs the full analysis; V6/V7 follows from the same successful run and sends the validated comprehensive report. |

The former fixed 22:30 UTC close schedule and the 0–60 second random startup sleep are no longer part of the production path.

## Open-session follow-up

The open workflow continues to re-evaluate non-terminal states later in the session. State-signature and terminal caches suppress unchanged or completed decisions. The first pass is intentionally conservative: a thin opening sample may authorize continued observation, but it cannot bypass a close-plan `REJECTED` state, stop/invalidation boundary, or missing risk plan.

The entry-timing layer now publishes:

- ideal entry price selected from multiple causal candidates;
- acceptable entry zone;
- no-chase-above price;
- candidate source and research touch/EV scores;
- stop, targets, and position limits from the existing hard risk contract.

These research scores are not represented as calibrated win probabilities.

## Email configuration

The low-latency open/close path uses the same repository values:

```text
EMAIL_SENDER
EMAIL_PASSWORD
EMAIL_RECEIVERS
```

Optional values:

```text
EMAIL_SENDER_NAME
SMTP_HOST
SMTP_PORT
```

Common Gmail, QQ/Foxmail, 163/126, and Outlook SMTP hosts are inferred automatically. `SMTP_HOST` / `SMTP_PORT` are available for other providers.

## Research and calibration

`02b-us-open-research-ledger.yml` persists the open decisions plus reusable 1-minute and 5-minute intraday bars. Settled outcomes compare optimized-entry execution with immediate entry and report entry-timing alpha.

`02c-us-open-policy-calibration.yml` evaluates the global timing policy and separate market-regime slices. When an eligible Challenger exists on the weekly scheduled run, the workflow creates a dedicated pull request containing only approved timing tunables and/or regime overrides. CI and review remain required before production changes.

## Scheduling caveat

GitHub Actions `schedule` is not a hard real-time scheduler. A 09:30 or 16:00 candidate can still enter the GitHub queue late. The dense open candidates, EDT/EST runtime gates, lightweight dependencies, and separate close-flash workflow minimize avoidable application-side latency; they cannot guarantee second-level delivery from GitHub-hosted runners.
