# V6.4 Research Governance

V6.4 completes the research-governance layer around the existing V6.3 Accuracy / Alpha Lab. It does **not** change the production Champion, 5D/10D/20D direction thresholds, daily report, notification routing, or automatic-trading behavior.

The goal is to separate four questions that were previously too easy to mix together:

1. Did a model look good in the historical replay?
2. Is the result statistically credible after testing many model/horizon combinations?
3. Does the result survive realistic friction and different years/directions?
4. Does the **same frozen hypothesis** continue to work on genuinely new outcomes?

## Runtime contract

The weekly job still starts from the isolated, read-only-safe three-year research database and the existing strict no-lookahead replay. `scripts/run_v6_accuracy_weekly.py` loads the history once, builds one observation sequence, and reuses those observations for both the V6.3 summary and V6.4 governance evidence before writing the JSON and Markdown Artifact.

V6.4 adds these top-level method contracts:

```text
research_governance_version=v6.4
alpha_yearly_walk_forward_method=global_alpha_non_overlap_then_calendar_year_v1
alpha_calibration_common_timeline_method=global_alpha_non_overlap_then_fixed_margin_buckets_v1
alpha_multiple_testing_method=holm_bonferroni_exact_one_sided_binomial_v1
alpha_direction_diagnostics_method=global_alpha_non_overlap_by_direction_v1
alpha_cost_sensitivity_method=global_alpha_non_overlap_fixed_total_cost_bps_v1
forward_alpha_watch.method=frozen_candidates_global_non_overlap_post_freeze_v2
```

All new metrics are research-only.

## 1. Alpha yearly walk-forward

V6.3 already had yearly walk-forward for absolute directional accuracy. V6.4 adds the same stability view for the **SPY-relative Alpha Target**.

For each model and horizon:

1. filter to Alpha-eligible bullish/bearish observations;
2. select the non-overlapping set once on the complete timeline;
3. partition that same independent set by calendar year.

Each year reports raw and independent sample count, Alpha Target hit rate, Wilson 95% confidence interval, average Alpha Spread, and median Alpha Spread.

The selector never restarts on January 1, so an overlapping late-December / early-January pair cannot both be presented as independent evidence.

## 2. Common-timeline Score Calibration

V6.3 keeps a policy-style calibration view:

```text
bucket -> filter -> non-overlap
```

That view is useful for asking what would happen if only one score bucket were traded. It is not ideal for comparing buckets with each other because every bucket can select a different set of dates.

V6.4 therefore adds a second, stricter comparison view:

```text
all Alpha-eligible observations
        ↓
one global non-overlapping timeline
        ↓
0-2pt / 2-5pt / 5-10pt / 10pt+ buckets
```

Now the buckets are mutually exclusive partitions of the same independent evidence set. This prevents an overlapping observation from becoming "independent" merely because it belongs to another score bucket.

The existing V6.3 calibration is preserved; the new view does not silently change its meaning.

## 3. Multiple-testing control

The main historical Alpha family is the complete set of model × horizon Alpha Target tests currently present in the weekly result.

For each test V6.4 calculates an exact one-sided binomial p-value against:

```text
H0: Alpha Target hit probability <= 50%
H1: Alpha Target hit probability > 50%
```

It then applies Holm-Bonferroni family-wise error control.

A challenger can become an `alpha_research_candidate` only when all of the following are true:

- it is not the production Champion;
- independent Alpha sample count reaches the existing research/promotion floor;
- Wilson 95% lower bound is above 50%;
- average Alpha Spread is positive;
- Holm-adjusted p-value is below 0.05.

This candidate flag is deliberately separate from the existing directional Promotion Gate and has no production side effect.

## 4. Direction diagnostics

Every model/horizon receives bullish and bearish Alpha diagnostics on the same global independent timeline.

This reports direction share, raw and independent sample count, Alpha hit rate and Wilson interval, and average/median Alpha Spread.

The purpose is to detect whether an apparently good aggregate result is mostly a long-market effect, a short-side effect, or a balanced signal.

## 5. Cost sensitivity

The weekly report stress-tests the global independent Alpha Spread under fixed total friction assumptions:

```text
0 bps
10 bps
20 bps
40 bps
```

For each level it reports sample count, average net Alpha Spread, median net Alpha Spread, percentage of observations with net Alpha > 0, and a Wilson 95% interval for that positive-net rate.

This remains a simplified research stress test. It does not model symbol-specific bid/ask spread, slippage, borrow availability, borrow cost, financing, dividends, tax, market impact, or portfolio position sizing. It must not be described as executable P&L.

## 6. Frozen candidate forward watch

The V6.3 run on 2026-08-10 found two hypotheses worth watching:

```text
momentum_focus · 10D
relative_strength_focus · 20D
```

V6.4 freezes them at:

```text
2026-08-10
```

The freeze is not only `(variant, horizon)`. It also records the implementation identity needed to decide whether later observations are comparable with the discovery hypothesis:

- `ACCURACY_LAB_VERSION`;
- `SHADOW_VARIANT_REVISION`;
- bullish/bearish threshold for the horizon;
- existing `_variant_identity()` for both STOCK and ETF shadow profiles.

The discovery definitions are intentionally hard-coded into the frozen candidate specification. At every weekly run V6.4 reconstructs the current identities from `shadow_profiles()` and the current thresholds/revisions, then verifies them against the frozen definitions.

For each compatible frozen candidate, the global non-overlapping Alpha timeline is partitioned into:

- `discovery`: `as_of < 2026-08-10`;
- `forward`: `as_of >= 2026-08-10`.

If a later change modifies the shadow multiplier, base profile, relevant threshold, Shadow revision, or Accuracy Lab revision, the watch row becomes:

```text
status=definition_drifted
definition_matches_freeze=false
```

The post-change replay values may still be shown for diagnosis, but they cannot become forward confirmation for the old frozen hypothesis and can never reach `ready_for_manual_review`. A materially new model definition therefore needs a new explicit frozen hypothesis rather than silently inheriting the old track record.

The candidate list is not reselected when new data arrives. This prevents later weekly runs from moving the goalposts by choosing whichever historical model/horizon currently looks best.

The two frozen forward hypotheses also receive Holm correction as a fixed family. Compatible definitions use these statuses:

- `waiting_for_outcomes`: no post-freeze mature independent samples yet;
- `collecting`: some post-freeze samples, but below the minimum forward sample floor;
- `not_confirmed`: enough samples exist but the confirmation gate fails;
- `ready_for_manual_review`: enough samples, Wilson lower bound > 50%, positive average Alpha, and Holm-adjusted p < 0.05.

`definition_drifted` is a separate fail-safe state. Even `ready_for_manual_review` does not change production automatically.

## 7. Production governance

The weekly payload hard-codes the following safety state:

```text
auto_promotion=false
auto_weight_tuning=false
research_governance.production_change_allowed=false
research_governance.automatic_production_action=none
research_governance.manual_review_required=true
```

Historical research may nominate hypotheses. Production changes still require enough forward/out-of-sample evidence, explicit review of statistical/regime/direction/cost evidence, a separate PR, CI, and an explicit rollback path.

No weekly job rewrites production weights, score thresholds, market-regime gates, trade plans or notification behavior.

## Weekly report sections

The Markdown Artifact retains all V6.3 sections and adds:

```text
V6.4 统计治理摘要
Alpha 多重检验（全模型 × 全周期）
Champion Alpha 年度 Walk-forward
Champion Common-timeline Score Calibration
Champion 方向暴露诊断
Champion Alpha 成本敏感度
冻结候选 Forward Watch
```

The JSON contains the same evidence for every model/horizon, not only Champion, including frozen and current candidate definitions for audit.

## Fail-closed validation

Before the weekly Artifact is written, `validate_research_governance()` checks the V6.4 contract:

- Alpha yearly raw/non-overlapping partitions reconcile to the total Alpha Target sample counts;
- common-timeline score buckets plus unscored observations reconcile to the same independent timeline;
- bullish/bearish direction partitions reconcile to the same independent timeline;
- cost grids keep identical sample counts and higher fixed costs cannot improve average net Alpha;
- raw and Holm-adjusted probabilities remain valid and adjusted p-values cannot be more optimistic than raw p-values;
- the frozen candidate set and frozen definitions cannot silently drift;
- a definition-drifted candidate cannot be marked review-ready;
- automatic promotion and automatic weight tuning remain disabled.

A contract violation fails report generation instead of publishing a misleading "green" research Artifact.

## Interpretation

A result should not be promoted because one historical row has a high hit rate. A stronger evidence chain is:

```text
historical signal
→ enough global non-overlapping samples
→ positive Alpha Spread
→ Wilson interval
→ multiple-testing correction
→ yearly/direction/regime stability
→ friction sensitivity
→ immutable frozen definition
→ genuinely new forward confirmation
→ manual review
→ separate production change
```

This is the V6.4 definition of a complete research-to-production boundary.
