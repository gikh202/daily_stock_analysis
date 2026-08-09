# V6.2 Accuracy Lab

V6.2 adds a research-only accuracy improvement layer on top of the existing V6.1 production forecast. It does **not** auto-trade, auto-tune production weights, or auto-promote a challenger.

## Goals

- distinguish raw sample count from effectively independent/non-overlapping samples;
- report 95% Wilson confidence intervals instead of treating one hit-rate number as certainty;
- evaluate BUY_SETUP plans with conservative future-OHLC execution rules;
- track win rate, expectancy, R multiple, Profit Factor and drawdown for actual trade plans;
- run multiple deterministic challenger weight profiles in shadow mode;
- compare challengers against the production champion on the same future bars;
- prevent overfitting by requiring a separate promotion research gate;
- keep all results auditable in SQLite + JSON + Markdown artifacts.

## Daily flow

```text
V4 production analysis
        ↓
V6 champion signal (unchanged)
        ↓
5D / 10D / 20D live outcomes
        ↓
V6.2 Accuracy Lab
        ├── Wilson 95% confidence interval
        ├── non-overlapping sample validation
        ├── BUY_SETUP execution replay
        └── shadow challengers
                ├── trend_guard
                ├── momentum_focus
                └── relative_strength_focus
        ↓
v6_accuracy_lab.json / v6_accuracy_lab.md
```

## Non-overlapping validation

Daily 20D forecasts overlap heavily. For example, Monday and Tuesday 20D forecasts share most of the same future window, so 100 daily observations are not equivalent to 100 independent experiments.

V6.2 therefore reports two views:

1. all matured observations;
2. a non-overlapping subset per symbol/horizon, where a new sample is accepted only after at least the relevant number of trading bars has elapsed from the previous accepted sample.

Promotion research uses the non-overlapping view.

## Statistical confidence

Directional hit rate and BUY_SETUP execution win rate include a 95% Wilson confidence interval.

A point estimate such as `61%` is never considered stable by itself. The research state is:

- `insufficient_data`: independent sample count is below the configured floor;
- `measurable_unproven`: enough samples exist, but the lower confidence bound does not exceed 50%;
- `evidence_above_chance`: enough samples exist and the 95% lower confidence bound exceeds 50%.

The default display floor remains 50 samples. Challenger promotion research defaults to at least 100 non-overlapping samples.

## BUY_SETUP execution replay

Only production `BUY_SETUP` decisions are treated as trades. `WATCH`, `WAIT`, and `AVOID` are not silently converted into entries.

The simulator uses the production trade plan:

- entry zone;
- stop loss;
- first target;
- maximum holding period (default 20 future trading bars);
- configurable round-trip cost (default 10 bps).

Conservative rules:

- entry requires a future OHLC bar to overlap the entry zone;
- if stop and target are both touched in the same daily bar, stop is assumed first;
- a gap below stop exits at the worse opening price when available;
- if neither stop nor target is reached, the position exits at the last close in the holding window.

Metrics include:

- trade win rate + 95% CI;
- average/median return;
- average R multiple;
- Profit Factor;
- maximum drawdown of the sequential trade-return curve;
- target-hit and stop-hit rates;
- breakdown by instrument type and market regime.

## Champion / Challenger

The production V6 weighting remains the Champion. V6.2 creates three shadow-only variants:

- `trend_guard`: increases durable trend weight;
- `momentum_focus`: increases short-term momentum/volume emphasis;
- `relative_strength_focus`: increases market/sector relative-strength emphasis.

Each shadow forecast is persisted in the V6 SQLite database and matures on the same 5D/10D/20D future trading bars.

A challenger is only marked as a **research promotion candidate** when all of the following are true for a horizon:

- both champion and challenger have at least the configured promotion sample floor using non-overlapping samples;
- challenger hit rate is at least 2 percentage points higher;
- challenger 95% CI lower bound is above 50%;
- challenger CI lower bound is not worse than the champion lower bound;
- SPY excess return is not worse when benchmark data is available.

This flag never changes production weights automatically.

## Persistence

V6.2 adds migration-safe tables to `v6_data/v6_daily.db`:

```text
v6_shadow_forecasts
v6_shadow_outcomes
v6_trade_outcomes
```

The existing `v6_signals` and `v6_outcomes` champion history remain unchanged.

## Artifacts

Each V6 daily run produces:

```text
v6_reports/v6_accuracy_lab.json
v6_reports/v6_accuracy_lab.md
```

The machine-readable daily payload also contains `accuracy_lab`, and `v6_run.json` exposes the lab status and any research promotion candidates.

## Configuration

Optional repository variables:

```text
V6_ACCURACY_LAB_COST_BPS=10
V6_ACCURACY_LAB_MAX_HOLDING_BARS=20
V6_ACCURACY_LAB_PROMOTION_MIN_SAMPLES=100
```

The defaults are intentionally conservative. Increasing/decreasing them changes research evaluation only; it does not directly change the production V6 forecast.

## Anti-overfitting policy

V6.2 explicitly keeps these disabled:

```text
auto_promotion=false
auto_weight_tuning=false
```

The intended process is:

1. collect champion and challenger live shadow outcomes;
2. inspect non-overlapping accuracy and confidence intervals;
3. compare SPY/QQQ excess return and trade-plan economics;
4. verify stability by market regime and instrument type;
5. run strict no-lookahead historical replay separately;
6. only after out-of-sample evidence is stable, create a reviewed code change to promote a challenger.

A challenger must never be tuned and evaluated on the exact same sample and then promoted automatically.
