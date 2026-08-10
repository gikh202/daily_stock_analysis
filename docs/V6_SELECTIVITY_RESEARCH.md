# V6.2 Confidence Selectivity Research

This note documents the research-only confidence/selectivity slices emitted by the V6.2 weekly Accuracy Replay.

## Why this exists

The main V6.2 replay measures all Champion/Challenger forecasts, including neutral forecasts that are treated as cash in the direction-aware strategy return. The first live historical run showed that the Champion 5D direction hit rate could be statistically above 50% while the average strategy excess versus long-only SPY remained negative.

That does not prove that the forecast has no economic value. One possible explanation is that low-conviction or neutral periods dilute the useful directional signals. The selectivity analysis therefore asks a narrower research question:

> If the system trades only directional forecasts that exceed the existing bullish/bearish trigger by a larger score margin, do hit rate and direction-strategy SPY excess improve as participation falls?

This is diagnostic evidence only. It does not modify production thresholds, Champion weights, promotion logic, or daily reports.

## Signal margin definition

Each horizon already has fixed production direction thresholds in `src/v6_daily/accuracy.py`.

For a bullish forecast:

```text
signal_margin_points = score - bullish_threshold
```

For a bearish forecast:

```text
signal_margin_points = bearish_threshold - score
```

Neutral forecasts have no directional margin and are excluded from these slices.

The weekly replay evaluates fixed minimum margins:

```text
0pt, 2pt, 5pt, 10pt
```

A `0pt` slice therefore means every directional forecast that already crossed the normal production direction threshold. Higher slices require progressively more distance beyond that same threshold.

## Non-overlapping semantics

The contract is:

```text
selectivity_analysis_method=directional_margin_filter_then_global_non_overlap_v1
```

For each model variant and horizon, the replay:

1. filters the full historical observation stream to directional signals meeting the selected minimum margin;
2. performs non-overlapping selection on that filtered stream across the complete symbol/horizon timeline;
3. computes the same hit-rate, Wilson 95% CI, direction-strategy return and SPY excess metrics used elsewhere in V6.2.

Filtering happens **before** non-overlapping selection intentionally. A weak or neutral signal does not reserve a future research trading window when the hypothetical selective policy would not have traded it. This makes the slice answer the actual "trade less, but only when conviction is stronger" question.

The selector still runs globally across each symbol timeline. It does not restart by calendar year.

## Output fields

Each `results[]` item in `v6_accuracy_weekly.json` now contains `selectivity_analysis` entries with:

- `min_margin_points`: fixed margin threshold;
- `participation_rate_pct`: qualifying raw observations divided by all raw observations for the model/horizon;
- `directional_capture_rate_pct`: qualifying raw observations divided by all directional observations for the model/horizon;
- `raw`: metrics for every qualifying observation;
- `non_overlapping`: metrics for the qualifying globally non-overlapping observations.

The Markdown weekly report displays Champion slices for 5D/10D/20D. JSON keeps the same slices for every Challenger so research comparisons remain auditable without making the human-facing report excessively large.

## Interpretation rules

A stronger margin is not automatically better. Useful evidence should show a reasonable combination of:

- enough non-overlapping samples to avoid unstable small-N conclusions;
- directional hit rate and Wilson interval that improve or remain robust;
- direction-strategy SPY excess that improves materially rather than only hit rate;
- participation that remains large enough to be operationally meaningful;
- similar behavior across later weekly runs instead of a one-run optimum.

Do not select a production threshold by simply choosing the best historical row. That would turn this diagnostic into an in-sample parameter search. Any threshold change requires separate out-of-sample evidence and a reviewed production code change.

## Safety boundaries

The following remain unchanged:

```text
auto_promotion=false
auto_weight_tuning=false
```

The selectivity slices are not referenced by the Challenger promotion gate. They are research evidence only.

The historical replay remains limited to reconstructible price/volume/benchmark features. Current SEC/FRED snapshots are excluded from historical dates to avoid look-ahead leakage.

Direction-strategy returns remain gross, unleveraged research returns with trading costs excluded. The separate BUY_SETUP execution replay remains the appropriate place for cost-aware trade-plan economics.
