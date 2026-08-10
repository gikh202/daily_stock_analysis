# V6.3 Alpha Target Lab

V6.3 extends the V6.2 weekly Accuracy Replay with a research-only objective that asks a stricter question: does a directional forecast also predict **relative performance versus SPY**?

The production V6 Champion, direction thresholds, daily report signals, notifications, automatic promotion policy, and automatic weight tuning remain unchanged.

## Why add an Alpha Target

Absolute direction accuracy and economic outperformance are different targets.

Example:

```text
forecast: bullish
stock future 5D return: +2%
SPY future 5D return:   +5%
```

The old absolute-direction label counts this as a bullish hit because the stock rose. The Alpha Target counts it as a miss because the stock underperformed SPY by 3 percentage points.

V6.3 therefore reports both views instead of replacing V6.2 evidence.

## Alpha Target definition

The payload contract is:

```text
alpha_target_method=spy_relative_directional_filter_then_global_non_overlap_v1
```

For every historical directional forecast whose future SPY return is available:

```text
underlying_alpha_pct = stock_future_return_pct - spy_future_return_pct
```

The SPY benchmark window follows the **actual calendar endpoints of the stock observation**, not an independently advanced count of SPY sessions. For a stock observation from `start_date` to its real `end_date`, V6.3 uses the latest SPY close at or before each endpoint. This matters for A-share/HK/US holiday mismatches: the stock and benchmark are compared over the same calendar interval instead of silently ending on different dates.

A bullish forecast is an Alpha Target hit when:

```text
underlying_alpha_pct > 0
```

A bearish forecast is an Alpha Target hit when:

```text
underlying_alpha_pct < 0
```

Neutral forecasts are excluded from Alpha Target accuracy. They are still preserved in the existing V6.2 absolute-direction and cash-strategy research views.

For a compact economic diagnostic, V6.3 also reports a directional relative-value research return:

```text
alpha_trade_return_pct = directional_position * underlying_alpha_pct
```

where bullish is `+1`, bearish is `-1`, and neutral is excluded.

This is a **gross research spread**, not executable portfolio P&L. It excludes trading costs, slippage, financing, borrow availability/cost, dividends, tax, and position-sizing constraints.

## Independent-sample semantics

Alpha Target results expose both:

- `raw`: every eligible directional observation;
- `non_overlapping`: a symbol/horizon subset selected only after Alpha-eligible observations are filtered, so a forecast that would not participate in the Alpha research policy does not reserve a future outcome window.

The same horizon spacing rule used elsewhere in V6.2 is applied. This reduces the false impression that heavily overlapping 5D/10D/20D daily forecasts are independent experiments.

## Fixed Score Calibration

The payload contract is:

```text
alpha_calibration_method=fixed_directional_margin_buckets_v1
```

V6.3 does **not** search for a historically optimal confidence threshold. Instead it uses four fixed, mutually exclusive signal-margin buckets:

```text
0-2pt
2-5pt
5-10pt
10pt+
```

Signal margin is still measured from the existing production bullish/bearish threshold for that horizon. The buckets answer a calibration question:

> As the score moves farther beyond the existing direction threshold, does future relative SPY performance improve in a reasonably monotonic and statistically credible way?

Each bucket reports raw/non-overlapping Alpha Target sample count, Alpha hit rate, Wilson 95% confidence interval, and average/median directional Alpha Spread where available.

The buckets are diagnostics only. The weekly job never chooses the best row and never rewrites a production threshold.

## SPY Regime Matrix

The payload contract is:

```text
regime_matrix_method=global_alpha_non_overlap_then_asof_spy_regime_partition_v1
```

Every regime label is computed from SPY data available **at or before the forecast as-of date**. If the exact forecast calendar date has no SPY bar because another market is open while the U.S. market is closed, the classifier uses the latest earlier SPY bar instead of assigning `unknown` solely because of the holiday mismatch.

Trend uses trailing SPY 20D and 60D returns:

- `up`: both are positive;
- `down`: both are negative;
- `mixed`: their signs do not agree;
- `unknown`: insufficient history.

Volatility compares trailing annualized SPY 20D realized volatility with trailing 60D realized volatility:

- `expanding`: 20D volatility is greater than or equal to 60D volatility;
- `contracting`: 20D volatility is below 60D volatility;
- `unknown`: insufficient history.

For independent evidence, V6.3 first selects one global non-overlapping Alpha Target series per symbol/horizon and **then** partitions that same series by regime. This prevents each regime from restarting the selector and accidentally double-counting overlapping windows around regime transitions.

The matrix is descriptive research evidence. It does not automatically enable/disable production forecasts in any regime.

## Weekly report

`v6_accuracy_weekly.json` now keeps the complete Alpha Target, fixed calibration, and regime matrix for every Champion/Challenger result.

The Markdown weekly report emphasizes Champion evidence with three additional sections:

```text
Champion Alpha Target（相对 SPY）
Champion Score Calibration（固定分数余量桶）
Champion SPY Regime Matrix
```

The older V6.2 direction, selectivity, yearly walk-forward, and Challenger promotion sections remain available for comparison.

## Safety and anti-overfitting boundaries

The following remain hard-disabled:

```text
auto_promotion=false
auto_weight_tuning=false
```

V6.3 Alpha Target evidence is not referenced by the existing Challenger Promotion Gate. The production Champion weights and bullish/bearish thresholds are unchanged.

A positive historical Alpha Target result, calibration bucket, or regime cell is not sufficient reason to change production behavior. Any future threshold, regime gate, or weighting change requires a separate reviewed change and genuinely out-of-sample/holdout evidence.

The historical replay remains limited to reconstructible price/volume/benchmark features. Current SEC/FRED snapshots are excluded from historical dates to avoid look-ahead leakage.

## Interpretation checklist

Useful evidence should combine several properties rather than optimizing one number:

- enough non-overlapping samples;
- Alpha Target hit rate with a credible Wilson interval;
- positive or improving average Alpha Spread;
- similar behavior across later weekly runs and years;
- reasonable calibration rather than a single isolated high-margin bucket;
- consistency across more than one market regime, or a separately validated regime hypothesis.

The purpose of V6.3 is to determine whether the existing directional score contains repeatable **relative-value information**, not to manufacture a better backtest by selecting the historically best threshold or regime.

## V6.4 research-governance continuation

V6.3 remains the definition of the SPY-relative target, score-margin calibration and regime features. V6.4 adds the governance layer around those metrics rather than changing their meaning.

The weekly pipeline now additionally evaluates Alpha yearly walk-forward stability, a common global non-overlapping timeline for fair bucket-to-bucket comparison, Holm-Bonferroni correction across the model × horizon Alpha family, bullish/bearish exposure diagnostics, fixed friction-cost sensitivity and a frozen forward-only watch list. See [`V6_RESEARCH_GOVERNANCE.md`](V6_RESEARCH_GOVERNANCE.md) for the complete contract.

These additions do not change the production Champion, production thresholds or existing V6.3 Alpha Target semantics. Historical evidence can nominate a research hypothesis, but promotion remains manual and requires forward/out-of-sample confirmation.
