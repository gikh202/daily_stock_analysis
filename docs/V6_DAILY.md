# V6 Daily Intelligence

V6 Daily is an isolated, deterministic US-stock daily intelligence layer built on top of the production analysis history already written by V4.

## Design goals

- Improve prediction auditability without replacing the stable production pipeline in one risky step.
- Keep numeric decisions deterministic. LLM prose never directly sets V6 Forecast, Opportunity, Quality, Risk, evidence coverage, or position size.
- Preserve missing data as missing evidence instead of silently substituting a neutral score.
- Evaluate predictions on future **trading bars** (5/10/20 sessions), not calendar days.
- Treat `AVOID` as **no position**, never as an implicit short.
- Keep V6 storage isolated in `v6_data/v6_daily.db` so rollback is deletion/disablement, not a production DB migration.
- Reuse existing GitHub Actions, YFinance-derived production history, notification channels and optional free public-data sources.

## Runtime flow

```text
每日股票分析 (existing production workflow)
        |
        | successful completion + persisted stock_analysis.db cache
        v
V6 AI 美股日报 (.github/workflows/03-v6-daily.yml)
        |
        +-- read analysis_history/context_snapshot
        +-- AlphaFeatureAdapter
        +-- deterministic V6 forecast
        +-- deterministic Alpha decision / trade plan
        +-- V6 SQLite history
        +-- mature 5/10/20 trading-session outcomes
        +-- opportunity ranking + delta + setup cards
        +-- prediction scoreboard
        +-- optional SEC/FRED evidence
        +-- existing notification channels
```

The original V4 advice is not overwritten by this workflow.

## Deterministic forecast

V6.0 uses a small, transparent directional forecast score:

| Feature | Weight |
|---|---:|
| Trend | 35% |
| Momentum | 25% |
| Relative Strength | 25% |
| Market Regime | 15% |

Only observed features participate. The directional forecast is neutral when observed directional-component coverage is below 50%.

Initial directional bands:

- `>= 60`: bullish
- `<= 40`: bearish
- otherwise: neutral

These thresholds are intentionally simple. They should not be automatically tuned until the V6 outcome database contains enough matured, out-of-sample evidence.

## Decision layer

Opportunity, Quality, Risk and trade-plan construction reuse the deterministic Alpha decision engine. This preserves the existing design principles:

- missing evidence lowers coverage;
- risk can veto actionability;
- position size is capped;
- low R:R setups are rejected;
- LLM prose is not an input to numeric score computation.

The legacy Alpha `confidence` storage column remains for schema compatibility, but V6 exposes it as `evidence_coverage`. It is **not** a calibrated win probability.

## Corrected validation semantics

V6 updates the Alpha validation layer:

- `BUY_SETUP`: included in long strategy-return metrics.
- `WATCH`: no position.
- `WAIT`: no position.
- `AVOID`: no position; evaluated separately with avoidance accuracy.
- future explicit `SHORT_SETUP`: can add short-return semantics later.

Validation now exposes:

- buy directional hit rate;
- avoidance hit rate;
- false avoid rate;
- average avoided return/downside;
- Opportunity IC (Spearman);
- Forecast Score IC (V6 store);
- signal-sequence risk proxies;
- evidence coverage;
- 5/10/20 trading-session outcome statistics.

The default research sample floor is **50**. `insufficient_data` is the expected state while history is young; it must not be presented as a failed strategy or as proof of performance.

## V6 daily report

`v6_reports/v6_daily_latest.md` contains:

1. Market Pulse
2. Significant Changes versus the prior V6 signal for each symbol
3. Opportunity Ranking
4. Setup Cards
5. LLM Health
6. Prediction Scoreboard
7. Free Public Data Context
8. Run Health and methodology

`v6_reports/v6_daily_latest.json` contains the same machine-readable payload.

## LLM role

V6 intentionally narrows LLM responsibility.

LLM can contribute:

- qualitative catalyst text already present in the production structured result;
- qualitative risk text;
- narrative explanation produced by the existing analysis layer;
- health/fallback metadata.

LLM does **not** directly determine:

- V6 direction;
- Forecast Score;
- Opportunity / Quality / Risk;
- evidence coverage;
- trade-plan numeric levels;
- V6 outcome evaluation.

This makes V6 robust to the empty-response and schema-validation failures that can occur in the upstream LLM path: qualitative context may degrade, but the V6 numeric engine remains deterministic.

## Free public-data enrichment

V6.0 includes an optional evidence-only enrichment module using standard-library HTTP calls.

### SEC EDGAR

Set a repository variable or secret:

```text
SEC_USER_AGENT=your-app-name contact@example.com
```

V6 can then retrieve public SEC company ticker metadata and recent submissions (10-K, 10-Q, 8-K, 20-F, 6-K).

SEC evidence is displayed in the report but **does not change V6 numeric scores in V6.0**.

### FRED

Create a free FRED API key and store it as a repository secret:

```text
FRED_API_KEY=...
```

Current macro evidence series:

- DGS10 — US 10Y Treasury
- DGS2 — US 2Y Treasury
- BAMLH0A0HYM2 — US High Yield OAS
- VIXCLS — VIX

FRED values are context-only in V6.0.

### Enable / disable enrichment

Repository variable:

```text
V6_FREE_SOURCE_ENRICHMENT=true
```

The workflow defaults this flag to `true`, but no SEC/FRED call is made when the corresponding credential/identity is absent. Failures are best-effort and cannot fail the deterministic V6 engine.

The existing repository continues using its already-supported YFinance market data plus configured search providers such as SerpAPI or Brave. V6 adds no mandatory paid API.

## GitHub Actions settings

Optional repository variables:

```text
V6_DAILY_NOTIFY=true
V6_DAILY_MIN_SAMPLES=50
V6_DAILY_SCAN_LIMIT=5000
V6_FREE_SOURCE_ENRICHMENT=true
SEC_USER_AGENT=your-app-name contact@example.com
```

Optional repository secret:

```text
FRED_API_KEY=...
```

All existing notification secrets are reused. If `V6_DAILY_NOTIFY=true`, users may temporarily receive both the existing production report and the V6 report during the comparison period. Set it to `false` to collect V6 artifacts silently.

## Files

```text
src/v6_daily/
  __init__.py
  models.py
  engine.py
  store.py
  report.py
  free_sources.py

scripts/run_v6_daily.py
.github/workflows/03-v6-daily.yml
.github/workflows/ci-v6-daily.yml
tests/test_v6_daily.py
```

## Promotion policy

V6 should not replace production advice merely because its CI passes. Promotion requires matured evidence. Recommended sequence:

1. Keep V4 and V6 running in parallel.
2. Accumulate at least the configured minimum per relevant horizon/regime; 50 is only the initial research floor, not definitive proof.
3. Compare direction hit rate, Forecast/Opportunity IC, BUY setup performance and regime stability.
4. Increase the sample floor as history grows.
5. Promote only if V6 materially and consistently improves out-of-sample behavior without unacceptable risk degradation.

No automatic weight tuning or automatic trading is introduced by V6.0.
