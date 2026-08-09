# V6 Daily Intelligence

V6 is the deterministic accuracy and decision layer that runs after the production V4 analysis workflow. V4 continues to collect market/search/LLM research; V6 converts structured evidence into auditable multi-horizon forecasts, evaluates them on future trading bars, and produces the single Chinese V4+V6 fused report.

## V6.1 design goals

- Keep numeric decisions deterministic and auditable.
- Generate independent **5D / 10D / 20D** forecasts instead of validating one direction at three horizons.
- Keep LLM free-form prose out of numeric scoring.
- Preserve missing data as `None`; never manufacture a neutral 50.
- Separate STOCK and ETF scoring profiles.
- Prevent repeated same-day runs from inflating validation sample counts.
- Evaluate both absolute return and excess return versus SPY/QQQ when benchmark bars exist.
- Use SEC/FRED only under explicit point-in-time safety rules.
- Treat `AVOID` as no-position risk avoidance, never an implicit short.
- Keep V6 persistence isolated in `v6_data/v6_daily.db`.

## Runtime flow

```text
每日股票分析 (V4 production)
        |
        | stock_analysis.db + structured V4 research
        v
V6 AI 美股日报
        |
        +-- current free public evidence snapshot (SEC/FRED)
        +-- V4 structured feature adapter
        +-- instrument classification: STOCK / ETF
        +-- source/coverage gates
        +-- 5D / 10D / 20D deterministic forecasts
        +-- deterministic Alpha decision + Risk Engine 2.0
        +-- deduplicated V6 signal persistence
        +-- future trading-bar outcome maturation
        +-- SPY/QQQ excess-return validation
        +-- V4+V6 semantic fusion
        v
单封中文综合日报
```

## Multi-horizon forecast

V6.1 intentionally uses different evidence mixes for each horizon.

### Stock

- **5D:** short-term momentum, trend, relative strength, sector relative strength, volume confirmation, market regime.
- **10D:** trend, momentum, relative strength, sector relative strength, volume confirmation, market regime.
- **20D:** durable trend, relative strength, sector relative strength, fundamental quality, source-backed catalyst evidence, market regime.

### ETF

ETF forecasts do not use company earnings/fundamental weights. They emphasize trend, relative strength, volume and market regime.

The top-level legacy `forecast_score` / `direction` fields remain the **10D** forecast for backward compatibility. The database also stores the complete `horizon_forecasts` structure.

Forecast score is **not** a win probability. Probability-like interpretation is allowed only after enough matured historical samples exist for empirical calibration.

## Signal deduplication

A V6.1 signal uses the key:

```text
SYMBOL | effective_trade_date | engine_version
```

Therefore repeatedly forcing the same upstream workflow on the same effective trading day does not create multiple independent validation samples.

## STOCK / ETF separation

Known ETFs such as SPY, QQQ, QQQM, VOO, IVV, VTI and sector ETFs are routed through the ETF scoring profile. Stocks use the stock profile, including deterministic fundamental and sector-relative-strength evidence when available.

## SEC CompanyFacts / XBRL fundamentals

When `SEC_USER_AGENT` is configured and free-source enrichment is enabled, V6.1 can retrieve SEC CompanyFacts and derive deterministic metrics such as:

- Revenue YoY
- Operating Income YoY
- Net Income YoY
- Operating Margin
- Net Margin
- Operating Cash Flow / CapEx / Free Cash Flow
- FCF Margin
- Cash
- Debt
- Diluted-share change

Fundamental quality is **coverage-gated**. An incomplete CompanyFacts snapshot below the configured component-coverage floor is displayed for context but is not given the full numeric fundamental weight.

Debt handling prefers an SEC total-debt concept. If a filer reports current and noncurrent long-term debt separately, V6 sums the two only when they refer to the same reporting date. Partial debt evidence is marked incomplete and cannot create a full balance-sheet quality score.

## FRED macro evidence

With `FRED_API_KEY`, V6.1 currently reads:

- `DGS10` — US 10Y Treasury
- `DGS2` — US 2Y Treasury
- `BAMLH0A0HYM2` — US High Yield OAS
- `VIXCLS` — VIX

For each series the current snapshot includes recent level/change information. A deterministic macro-risk feature is derived from volatility, credit and rates evidence.

### Critical point-in-time rule

The daily workflow fetches a **current** SEC/FRED snapshot. It must never be applied to old analysis records during database rebuild/backfill, because that would introduce future information into historical samples.

V6.1 therefore applies the current external numeric snapshot only to the newest effective trade-date bucket when that trade date is recent. Older/backfilled signals are built without the newly fetched SEC/FRED numeric context. Current public data may still be displayed in the report as current context.

Historical SEC/FRED point-in-time replay would require an explicitly date-scoped data source and is not fabricated by this implementation.

## Catalyst evidence

Free-form LLM text such as `positive_catalysts` remains qualitative only.

A catalyst can influence a numeric score only if the structured event contains:

- signed direction;
- materiality/importance;
- a non-empty traceable source (`source` / `source_type`);
- optional freshness/reliability metadata.

Structured payloads with missing/unknown/LLM-only source provenance are rejected from numeric scoring.

## Risk Engine 2.0

Risk scoring can consume available deterministic evidence from:

- realized volatility;
- earnings/event proximity;
- gap risk when present in the structured snapshot;
- trend-breakdown proximity using price/support/ATR;
- FRED macro risk when point-in-time safe;
- data-quality risk.

Missing risk evidence reduces coverage rather than becoming a fake neutral value.

## Trade plan

The trade-plan gate remains deterministic. Risk can reduce actionability and position size, and low R:R setups are downgraded. V6.1 uses an ATR-based entry-zone width so an actionable setup is not presented as a meaningless `[price, price]` range.

## Validation and scoreboard

V6 stores each horizon's own forecast score and direction with its matured outcome. Validation includes:

- 5D / 10D / 20D directional hit rate;
- BUY_SETUP hit rate;
- AVOID / false-avoid statistics;
- MFE / MAE;
- Forecast Score IC (Spearman);
- Opportunity IC;
- SPY / QQQ benchmark return when available;
- average excess return versus SPY / QQQ;
- Forecast Score IC versus SPY excess return;
- market-regime breakdown;
- empirical score calibration buckets after the minimum sample threshold.

The initial research floor remains **50 samples per relevant horizon/bucket**. `insufficient_data` is expected while history is young and is not proof of poor performance.

## Historical replay

V6.1 adds a strict no-lookahead replay CLI using only data available at each historical as-of date.

```bash
python scripts/run_v6_replay.py \
  --stock-db data/stock_analysis.db \
  --codes MSFT,GOOGL,QQQM,VOO \
  --output v6_reports/v6_replay.json
```

The replay builds rolling market-bar features using observations at or before each as-of date, then evaluates future 5/10/20 trading-bar returns. It never uses current SEC/FRED snapshots for historical dates.

If SPY/QQQ history exists in `stock_daily`, replay/production validation can calculate benchmark-relative outcomes. If those benchmark bars do not exist, the corresponding excess-return fields stay missing rather than being fabricated.

## Unified Chinese report

The final email/report is still a single semantic fusion of V4 and V6 rather than two Markdown reports appended together. V6.1 adds:

- instrument type (stock / ETF);
- 5D / 10D / 20D deterministic forecasts;
- per-horizon evidence coverage;
- SEC CompanyFacts fundamental context when available;
- FRED macro-risk context when available;
- existing V4 research drivers/risks and V6 deterministic execution constraints.

The compatibility `fusion_mode` remains `structured_v4_v6`; V6.1 is exposed as an accuracy layer so existing consumers are not broken.

## GitHub Actions settings

Repository variables:

```text
V6_DAILY_NOTIFY=true
V6_DAILY_MIN_SAMPLES=50
V6_DAILY_SCAN_LIMIT=5000
V6_FREE_SOURCE_ENRICHMENT=true
SEC_USER_AGENT=your-app-name contact@example.com
```

Repository secret:

```text
FRED_API_KEY=...
```

No new paid provider, server, GPU or additional GitHub workflow is required by V6.1.

## Important files

```text
src/v6_daily/
  accuracy.py
  accuracy_report.py
  engine.py
  free_sources.py
  models.py
  replay.py
  report.py
  store.py
  unified_report.py

scripts/run_v6_daily.py
scripts/run_v6_replay.py
.github/workflows/03-v6-daily.yml
.github/workflows/ci-v6-daily.yml
tests/test_v6_daily.py
tests/test_v6_accuracy_foundation.py
```

## Promotion policy

Do not claim that V6.1 is more accurate merely because its code or CI passes. The architecture now measures accuracy more correctly; actual improvement must be demonstrated by matured live outcomes and strict no-lookahead replay.

Recommended process:

1. Keep collecting one deduplicated live signal per symbol/effective date/engine version.
2. Run historical replay on sufficiently long stored daily history.
3. Compare 5D/10D/20D hit rate and Forecast IC separately.
4. Compare absolute return with SPY/QQQ excess return.
5. Inspect performance by market regime and instrument type.
6. Only recalibrate thresholds/weights when evidence is large enough and remains stable out of sample.
7. Never auto-tune on the same sample used to report performance.

No automatic order placement or unvalidated ML weight fitting is introduced by V6.1.
