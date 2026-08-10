# V6.2 Accuracy Lab

V6.2 adds a research-only accuracy improvement layer on top of the existing V6.1 production forecast. It does **not** auto-trade, auto-tune production weights, or auto-promote a challenger.

## Goals

- distinguish raw sample count from effectively independent/non-overlapping samples;
- report 95% Wilson confidence intervals instead of treating one hit-rate number as certainty;
- evaluate BUY_SETUP plans with conservative future-OHLC execution rules;
- track win rate, expectancy, R multiple, Profit Factor and drawdown for actual trade plans;
- run multiple deterministic challenger weight profiles in shadow mode;
- compare challengers against the production champion on the same future bars;
- run a strict no-lookahead Champion/Challenger historical replay using only historical price/volume/benchmark features;
- report yearly stability using both raw and globally non-overlapping samples;
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

Core trade metrics include:

- trade win rate + 95% CI;
- average/median return;
- average R multiple;
- Profit Factor;
- maximum drawdown of the sequential trade-return curve;
- target-hit and stop-hit rates.

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
- direction-aware strategy SPY excess return is not worse when benchmark data is available.

This flag never changes production weights automatically.

### Challenger identity rule

A variant name is part of its historical research identity. If a future code change materially changes a challenger's multipliers or evidence definition, the variant must be renamed/versioned (for example `trend_guard_v2`) rather than silently reusing the old identity. This prevents incompatible shadow samples from being pooled into one performance history.

## Strict historical Champion/Challenger replay

V6.2 also provides a separate strict no-lookahead replay:

```bash
python scripts/run_v6_accuracy_replay.py \
  --stock-db data/stock_analysis.db \
  --codes MSFT,GOOGL,QQQM,VOO \
  --min-samples 50 \
  --promotion-min-samples 100 \
  --output v6_reports/v6_accuracy_replay.json
```

For every historical as-of date, this replay builds features only from observations available at or before that date, creates the Champion and all Challenger forecasts, then evaluates future 5/10/20 trading-bar returns. It also reports non-overlapping samples, Wilson intervals, yearly walk-forward results, and direction-aware strategy/SPY excess returns when benchmark history exists.

Historical replay maps each forecast to a simple gross research position: `bullish=+1x`, `bearish=-1x`, and `neutral=0x` (cash). `avg_return_pct` is the average return of that direction-aware position, while `avg_excess_vs_spy_pct` is that strategy return minus the contemporaneous long-only SPY return. This makes Champion/Challenger return and Alpha metrics variant-specific instead of reusing the same underlying stock return for every model.

For auditability, the JSON also preserves `avg_underlying_return_pct` and `avg_underlying_excess_vs_spy_pct`, which describe the underlying symbol path independent of forecast direction. Historical direction-strategy returns are gross of trading costs and are not a substitute for the separate cost-aware BUY_SETUP execution replay.

### Yearly walk-forward stability views

Each `yearly_walk_forward` row now exposes two explicit metric blocks:

- `raw`: all replay observations whose as-of date falls in that calendar year;
- `non_overlapping`: the subset from the **same globally selected non-overlapping series** used by the main horizon statistics, partitioned by calendar year.

The non-overlapping selector runs across the complete symbol/horizon timeline **before** the results are split into years. It does not restart on January 1. This prevents a late-December forecast and an early-January forecast with overlapping 5D/10D/20D outcome windows from both being counted as independent merely because they fall in different calendar years.

For compatibility with older JSON readers, the pre-v2 flattened yearly metric fields remain present and continue to represent the raw yearly view. New research/report code should use the explicit `raw` and `non_overlapping` blocks. The payload identifies this contract with:

```text
yearly_walk_forward_method=raw_and_global_non_overlapping_by_calendar_year_v2
```

The Markdown weekly report displays both raw and non-overlapping yearly N, hit rate, strategy return and SPY excess; Wilson 95% CI is emphasized on the non-overlapping view.

Historical replay intentionally excludes **current** SEC/FRED snapshots. Without a true point-in-time historical SEC/FRED dataset, injecting today's official/macro values into old dates would be look-ahead leakage. The replay therefore measures the historically reconstructible price/volume/benchmark layer and labels that scope explicitly.

## Weekly isolated history backfill

The scheduled `V6 准确率研究周报` must not report a successful research result merely because a newly created production cache contains only a few recent trading days. The replay needs more than 60 lookback bars plus the longest 20-bar outcome horizon before it can create an observation.

The weekly workflow therefore prepares a separate research database before replay:

```text
production data/stock_analysis.db (read-only)
        ↓ SQLite backup
v6_research/stock_analysis_research.db
        ↓ existing YfinanceFetcher, fixed 3-year window
current target symbols + SPY + QQQ
        ↓
strict no-lookahead Champion/Challenger replay
```

Safety and provenance rules:

- the production SQLite database is opened read-only and is never used as the backfill write target;
- the clone is created with SQLite's backup API so the research job starts from a consistent production snapshot;
- historical OHLCV is fetched through the repository's existing `YfinanceFetcher` path instead of adding a parallel market-data implementation;
- the research clone uses a fixed three-year window and requires at least 81 bars per replay-eligible symbol;
- `SPY` and `QQQ` are backfilled as benchmark histories; both must meet the minimum history requirement;
- an individual target with insufficient history can be reported as partial, but at least one target plus both benchmarks must be replay-eligible;
- the workflow records source row counts before and after backfill and fails if the production source changed;
- the weekly validation now fails when `observations=0`, when Champion 5D/10D/20D results are missing, when independent samples/Wilson CI/SPY excess are absent, or when yearly raw/non-overlapping partitions do not reconcile to the horizon totals;
- the temporary research SQLite file is not uploaded as an artifact and does not replace the normal production cache.

The backfill audit sidecar is included in the weekly report artifact:

```text
v6_reports/accuracy_weekly/v6_accuracy_history_backfill.json
```

No new Secret or Repository Variable is required for this preparation step. YFinance is already an existing free fallback dependency of the project.

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

Historical replay is intentionally an explicit research command rather than an expensive daily job; its default output is:

```text
v6_reports/v6_accuracy_replay.json
```

The weekly workflow additionally uploads:

```text
v6_reports/accuracy_weekly/v6_accuracy_weekly.json
v6_reports/accuracy_weekly/v6_accuracy_weekly.md
v6_reports/accuracy_weekly/v6_accuracy_history_backfill.json
```

## Configuration

Optional repository variables:

```text
V6_ACCURACY_LAB_COST_BPS=10
V6_ACCURACY_LAB_MAX_HOLDING_BARS=20
V6_ACCURACY_LAB_PROMOTION_MIN_SAMPLES=100
```

All three variables are optional. The GitHub Actions workflows and the V6.2 runtime have matching built-in defaults, so the Accuracy Lab works without adding any new Secret or Variable. Configure repository Variables only when intentionally overriding the research assumptions.

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
3. compare direction-aware SPY excess return and trade-plan economics;
4. verify stability by market regime, instrument type, and yearly raw/non-overlapping evidence where sufficient samples exist;
5. run strict no-lookahead historical replay separately;
6. only after out-of-sample evidence is stable, create a reviewed code change to promote a challenger.

A challenger must never be tuned and evaluated on the exact same sample and then promoted automatically.
