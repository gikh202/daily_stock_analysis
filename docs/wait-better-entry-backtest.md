# WAIT_BETTER_ENTRY research contract

V7.1 separates the prior-close risk decision from intraday execution timing. `WAIT_BETTER_ENTRY` means the setup remains eligible but the timing model estimates that a better entry may be available within a short, explicit wait window.

## Runtime decision fields

Each timing decision JSON exposes:

- `expected_better_price`: reference price produced from evidence available at the evaluation time.
- `expected_wait_minutes`: the promised evaluation window for that reference price.
- `better_entry_reason`: stable research label for the wait rationale.
- `better_entry_score`: uncalibrated intraday heuristic score. It must not be described as a historical win probability until sufficient settled outcomes exist.

The timing model may use current price, current-session range, VWAP, recent momentum, intraday volatility, risk-bounded entry levels, stop loss, elapsed session time, and strict-as-of forecast probabilities. Historical/as-of replay must cut intraday bars at the evaluation timestamp; later bars, session close, and future daily extrema are prohibited as inputs.

## Research settlement

A WAIT signal is a success only if `expected_better_price` is touched **within `expected_wait_minutes`** after `signal_bar_time`. A target first reached after that window is a miss for that signal. The ledger stores `better_entry_hit`, `best_future_improvement_pct`, and `minutes_to_reference_better_price` using the same promised window.

For older decisions that do not contain `expected_wait_minutes`, settlement falls back to `recheck_minutes`, then to 30 minutes. The research window is capped to 1–120 minutes.

## Immediate-entry versus WAIT A/B replay

`scripts/backtest_wait_better_entry.py` replays the exact production V2 guard followed by the V7.1 timing model on the same 09:30–09:44 causal evidence. For each resulting WAIT signal it compares:

- immediate entry at the signal price, and
- WAIT entry at `expected_better_price` if touched within the promised window; otherwise the WAIT policy remains in cash for that session.

The output reports WAIT sample count, target hit rate, two distinct price-improvement measures, time to target, missed continuation rate, immediate and WAIT returns, MFE/MAE, diagnostic drawdown, and entry-timing alpha (`WAIT session return - immediate-entry session return`). `avg_best_price_improvement_pct` is the best observable low inside the promised wait window and is diagnostic only. `avg_realized_entry_improvement_pct` uses `(signal_price - expected_better_price) / signal_price` only for WAIT signals whose expected price was actually touched, so it is the promotion-relevant entry improvement. These are research diagnostics and do not automatically change production thresholds.

## Intraday history coverage

The workflow requests the available historical 1-minute data associated with the stored forecast history. Yahoo/yfinance can retain materially less than three years of 1-minute bars. Therefore `actual_session_start` and `actual_session_end` in the artifact are authoritative. A run must not be described as a three-year WAIT backtest unless those fields actually prove comparable coverage.

## Promotion rule

The backtest exposes a research-only promotion check. V7.1 remains unchanged unless all of the following are true:

- at least 20 comparable WAIT samples are available;
- the expected-better-price hit rate is above 50%;
- average realized entry-price improvement exceeds the fixed 0.10% research transaction-cost/slippage hurdle; and
- average entry-timing alpha versus immediate entry is positive.

The 0.10% hurdle is an explicit research assumption, not a broker-specific fee quote. Any future V7.2 confidence enhancement requires a separate reviewed production change; WAIT is not promoted into a new hard buy filter by this research workflow.
