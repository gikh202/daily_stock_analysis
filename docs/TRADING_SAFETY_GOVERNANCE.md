# Trading Safety and Model Promotion Governance

This document defines the non-negotiable safety contract for the U.S. stock decision pipeline.

## 1. Execution-state contract

The close layer exposes four production states:

- `FULL_APPROVED`: the deterministic plan is complete and execution-authorized.
- `CONDITIONAL_APPROVED`: the thesis is monitorable but execution still requires the declared condition/complete plan.
- `UNRESOLVED`: evidence is insufficient or the close layer cannot approve; this is not a permanent risk veto.
- `HARD_REJECTED`: a machine-readable hard blocker is active and the open layer must not upgrade it to a buy.

Legacy `REJECTED` packets are interpreted as `UNRESOLVED` unless a stable hard-reject reason is present.

Hard blockers include, at minimum, deterministic avoid/risk gates, sell/reduce directives, and non-trading contexts. The open layer may re-evaluate `UNRESOLVED`, but `HARD_REJECTED` is monotonic until a new close packet removes the hard blocker.

## 2. Portfolio-risk monotonicity

Single-name models propose risk; the portfolio overlay may only reduce that proposal.

The final trade plan is capped after forecast-plan generation using:

- existing single-name exposure;
- gross portfolio exposure;
- sector exposure when structured sector evidence is available;
- portfolio drawdown soft and hard gates.

Missing portfolio evidence is reported explicitly. It must never create additional risk capacity.

## 3. Forecast reliability

A probability-like model output is not sufficient for production execution by itself.

Production direction requires both:

1. the existing mature overall calibration gate; and
2. an out-of-sample strong-signal skill gate for decisive forecasts (`P(up) >= 58%` or `P(up) <= 42%`).

Strong-signal reliability must beat its own majority-class baseline. Broader fallback scopes receive a reliability haircut. Insufficient samples result in zero production trading weight, not a neutral fabricated sample.

Observed trailing return is momentum evidence only. It must not be reused as a future expected-return target. `expected_alpha_vs_spy_pct` is SPY-relative only; QQQ excess is retained separately as diagnostic evidence.

## 4. Entry Optimizer promotion

The Entry Optimizer remains research-only until execution-path validation passes.

Promotion evidence must model:

- limit-order fill/no-fill;
- stop/target first-touch;
- conservative handling when stop and target occur in the same 1-minute OHLC bar;
- entry and exit slippage;
- round-trip fees;
- opportunity cost of unfilled winning trades;
- session-date clustered bootstrap confidence intervals;
- drawdown versus immediate-entry execution.

A positive average result alone is not a promotion criterion.

## 5. Feature and macro governance

Feature redundancy is audited using pairwise correlation and leave-one-out contribution. The audit is advisory and cannot rewrite production weights automatically.

Macro risk may use rates, credit, volatility, real-yield and USD-liquidity evidence. Missing components are omitted from the weighted evidence set; they are never replaced by an invented neutral `50`.

Current external SEC/FRED snapshots may numerically enrich only the newest current canonical signals. They must not be injected into historical/backfilled forecasts.

## 6. Change-control requirements

Changes under trading, forecasting, portfolio-risk, open-confirmation, Entry Optimizer, and relevant workflow paths are CODEOWNED. The `V9 Trading Safety CI` workflow should be configured as a required status check before merging changes to these paths.

Recommended `main` branch/ruleset configuration:

- require pull requests before merge;
- require at least one approving review;
- require CODEOWNER review for owned files;
- require `V9 Trading Safety CI` plus existing repository CI checks;
- dismiss stale approvals after new commits;
- block force pushes and branch deletion;
- do not allow model/policy promotion PRs to auto-merge.

Repository administration/ruleset settings are intentionally outside application code. If the connected GitHub integration cannot write administration settings, the codebase records this requirement but must not claim the protection is active.

## 7. Broker boundary

This repository produces decision support and research artifacts. Broker/exchange order placement, fill reconciliation and account authorization require an explicitly configured broker integration, credentials, order lifecycle model and separate safety review. No model promotion may silently activate real-money order placement.
