# V5 Alpha Trading System Architecture

## 1. Objective

Evolve DSA from an AI research/report generator into an auditable trading decision-support system without destabilizing the proven V4 production pipeline.

The target is not “more indicators”. The target is a system that enforces the same questions a disciplined professional trader asks every day:

1. Is there an actual edge here?
2. Is the evidence complete enough to act?
3. What invalidates the thesis?
4. How much can be lost if wrong?
5. Does the setup fit the current market/portfolio risk budget?
6. Did this class of setup historically work after 5/10/20 trading days?

## 2. Non-negotiable invariants

1. **LLM does not own numeric trade decisions.** LLMs research and explain; deterministic/validated engines score and gate decisions.
2. **Forecast is immutable.** Market-phase or risk guardrails may change execution action/size, never rewrite the stored forecast.
3. **Missing data is not neutral data.** Missing features remain `None`, reduce coverage/confidence and may force `WAIT`.
4. **Risk has veto power.** Strong alpha cannot override hard risk limits.
5. **No order placement in Alpha Engine.** It produces a plan only.
6. **Every score is attributable.** Persist raw feature snapshot, feature/engine version, coverage, score and gate reasons.
7. **No online self-modifying weights.** Weight changes require mature out-of-sample evidence, versioning and tests.
8. **Fail open for optional intelligence; fail safe for trading action.** Provider failure must not crash research, but insufficient evidence must never create an actionable setup.
9. **No look-ahead leakage.** Outcome evaluation uses only trading bars after the original analysis timestamp.
10. **Portfolio overlay may only reduce risk.** It can never upgrade a weaker single-name decision or increase its position cap.
11. **Production V4 remains isolated during incubation.** Shadow errors must not block normal analysis, notification or production backtest.

## 3. Architecture

```text
                         ┌──────────────────────────────┐
                         │ Existing V4 Production      │
Providers ──────────────>│ context / forecast / report│────────> User reports
                         └──────────────┬───────────────┘
                                        │ immutable persisted snapshot
                                        v
                         ┌──────────────────────────────┐
                         │ V5 Feature Adapter           │
                         │ structured evidence only    │
                         └──────────────┬───────────────┘
                                        v
                         ┌──────────────────────────────┐
                         │ Deterministic Alpha Engine   │
                         │ quality/opportunity/risk     │
                         └──────────────┬───────────────┘
                                        v
                         ┌──────────────────────────────┐
                         │ Trade Plan / Risk Gate       │
                         │ WAIT/WATCH/BUY_SETUP/AVOID   │
                         └──────────────┬───────────────┘
                                        v
                         ┌──────────────────────────────┐
                         │ Portfolio Risk Overlay       │
                         │ reduce-only sizing           │
                         └──────────────┬───────────────┘
                                        v
                         ┌──────────────────────────────┐
                         │ Alpha Shadow SQLite          │
                         │ immutable feature snapshots  │
                         └──────────────┬───────────────┘
                                        v
                         ┌──────────────────────────────┐
                         │ 5D / 10D / 20D maturation   │
                         │ return / MFE / MAE / hit     │
                         └──────────────┬───────────────┘
                                        v
                         ┌──────────────────────────────┐
                         │ Scorecard / Offline promote  │
                         └──────────────────────────────┘
```

## 4. Implemented modules

### `src/alpha_engine/models.py`

Canonical immutable contracts:

- `AlphaFeatures`
- `AlphaDecision`
- `TradePlan`

A missing feature is represented by `None`, never an invented 50.

### `src/alpha_engine/engine.py`

Deterministic single-name decision layer:

- quality score;
- opportunity score;
- risk score;
- evidence coverage/confidence;
- hard risk veto;
- minimum 1.5R plan gate;
- bounded position cap;
- no LLM dependency.

Current shadow decisions:

- `BUY_SETUP`: strong opportunity, acceptable risk and adequate coverage;
- `WATCH`: plausible edge but not enough quality/risk to act aggressively;
- `WAIT`: evidence/setup is insufficient;
- `AVOID`: explicit risk veto or poor opportunity/risk combination.

### `src/alpha_engine/features.py`

Versioned deterministic adapter (`v5.0-shadow.1`) consuming only persisted structured V4 artifacts.

Initial trusted inputs:

- technical trend score;
- 20D/60D price momentum;
- SPY/QQQ excess-return relative strength;
- RVOL direction confirmation;
- upstream deterministic fundamental quality score when available;
- Market Regime + Breadth;
- realized volatility;
- earnings-event distance;
- data coverage.

Important design choice: **catalyst score stays unavailable until a deterministic evidence-backed classifier exists.** News count alone is not bullish or bearish.

### `src/alpha_engine/portfolio.py`

Reduce-only portfolio gate:

- max single-name exposure;
- max sector exposure;
- max gross exposure;
- soft/hard drawdown gates.

Final position cap is always `<=` the single-name Alpha cap.

### `src/alpha_engine/shadow_store.py`

Independent `alpha_shadow.db` so V5 schema cannot break V4 storage.

Persisted signal fields include:

- original `analysis_history_id`;
- symbol / analysis timestamp;
- engine and feature versions;
- quality/opportunity/risk/confidence;
- raw `AlphaFeatures` snapshot;
- trade plan;
- limitations/reasons/coverage diagnostics;
- baseline price and market regime.

Outcome maturity uses **future trading bars**, not calendar days, at 5D/10D/20D. It records:

- end return;
- maximum favorable excursion (MFE);
- maximum adverse excursion (MAE);
- directional hit for actionable directional states.

It is idempotent through unique `(analysis_history_id)` and `(signal_id, horizon_days)` constraints.

### `scripts/run_alpha_shadow.py`

One-command shadow replay:

```bash
python scripts/run_alpha_shadow.py \
  --stock-db data/stock_analysis.db \
  --alpha-db alpha_data/alpha_shadow.db \
  --report-dir alpha_reports
```

It only scans persisted V4 analysis history. It does not call market APIs or an LLM.

### `.github/workflows/01-alpha-shadow.yml`

Isolated production-like shadow workflow:

- runs after successful production workflow;
- has a scheduled fallback;
- restores latest valid V4 `data/` cache read-only;
- restores a separate Alpha history cache;
- validates both SQLite databases with `PRAGMA quick_check`;
- refuses signal-count regression;
- uploads `alpha_shadow.db` + scorecard as artifacts;
- never modifies the V4 production cache.

### `.github/workflows/ci-alpha-engine.yml`

Fast CI gate:

- Python 3.11 compile check;
- isolated Alpha tests with no project-wide test-fixture dependency;
- deterministic engine/adapter/portfolio/persistence/end-to-end runner coverage.

## 5. Feature philosophy

V5 deliberately avoids an indicator zoo.

The feature layer should represent economically distinct information classes:

| Class | Question |
|---|---|
| Trend | Is price structure supportive? |
| Momentum | Is the move persistent across medium horizons? |
| Relative strength | Is the asset outperforming investable alternatives? |
| Volume confirmation | Is participation confirming price direction? |
| Fundamental quality | Is the underlying asset/business quality supportive? |
| Catalyst | Is there deterministic evidence of a signed event? |
| Market regime | Is system-wide risk helping or hurting the setup? |
| Volatility risk | How violent is the current distribution? |
| Event risk | Is a binary event close enough to dominate the setup? |
| Data quality | How much of the required evidence is actually observed? |

Adding another oscillator is not a new information class.

## 6. Risk hierarchy

Risk is applied in layers:

```text
Single-name risk
      ↓
Trade-plan R:R gate
      ↓
Market regime risk
      ↓
Portfolio concentration / gross exposure
      ↓
Drawdown gate
      ↓
Final max position
```

A later layer may reduce or reject a setup. It may never increase an earlier risk budget.

## 7. Outcome learning and promotion policy

Shadow performance is descriptive only. V5 does **not** auto-change weights.

Minimum evaluation dimensions:

- 5D / 10D / 20D return;
- directional hit rate;
- MFE / MAE;
- performance by `BUY_SETUP/WATCH/WAIT/AVOID`;
- performance by Market Regime;
- opportunity/risk score buckets;
- relative performance vs SPY/QQQ when benchmark history is added to the outcome layer.

Promotion rules:

1. no production-pipeline failure regression;
2. deterministic replay must be stable;
3. no look-ahead leakage;
4. actionable plans always satisfy hard risk and R:R gates;
5. enough mature samples exist per relevant bucket;
6. V5 must improve decision quality or risk-adjusted outcome, not merely raw hit rate;
7. any weight/calibration change increments engine/feature version and repeats shadow validation.

## 8. Trader-facing Alpha Terminal target

The final terminal should prioritize decisions, not indicator panels.

For each symbol:

```text
MSFT
Decision        BUY_SETUP
Opportunity     82
Risk            31
Evidence        88%
Market          risk_on / broad

Entry zone      420 - 425
Invalidation    close below 405
Targets         450 / 470
R:R             2.3R
Max position    10%

Confirm before entry
- price holds the defined structure
- volume confirms the move

Do not trade if
- invalidation is hit
- portfolio/market risk veto activates

Shadow calibration
10D N=xx | hit rate xx% | avg return xx% | avg MAE xx%
```

The system's highest-value output is often **WAIT**. A professional tool should reduce bad trades, not maximize signal frequency.

## 9. Current deployment state

V5 remains **shadow-only**. It does not alter V4 email recommendations, Forecast, Guardrails or execution advice.

The next promotion milestone is not more code. It is enough mature shadow evidence to justify exposing V5 rankings in the report and, later, allowing the portfolio gate to influence execution sizing.
