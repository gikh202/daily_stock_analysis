# V5 Alpha Trading System Architecture

## Objective

Evolve the project from an AI research/report generator into an auditable trading decision-support system without destabilizing the proven V4 analysis pipeline.

## Non-negotiable invariants

1. **LLM does not own numeric trade decisions.** LLMs research and explain; deterministic/validated engines score and gate decisions.
2. **Forecast is immutable.** Market-phase or risk guardrails may change execution action/size, never rewrite the stored forecast.
3. **Missing data is not neutral data.** Missing features reduce coverage/confidence and may force WAIT.
4. **Risk has veto power.** Strong alpha cannot override hard risk limits.
5. **No order placement in Alpha Engine.** It produces a plan only.
6. **Every score is attributable.** Persist raw feature snapshot, weights/version, score, coverage and final gate reason.
7. **No online self-modifying weights.** Weight changes require mature out-of-sample evidence, versioning and tests.
8. **Fail open for optional intelligence; fail safe for trading action.** Provider failure must not crash research, but insufficient evidence must not create an actionable setup.

## Target architecture

```text
Providers -> Context/Feature Layer -> Alpha Engine -> Risk/Portfolio Gate
                                             |             |
                                             +-> Trade Plan+
                                                      |
Existing LLM Research/Explanation <------------------+
                                                      |
Persistence -> Outcome Maturation -> Calibration/Backtest
```

## Phase 1 (implemented on feature/v5-alpha-engine)

`src/alpha_engine/` introduces a deterministic sidecar engine. It is intentionally not wired into the production pipeline yet.

- `AlphaFeatures`: normalized observed features; unavailable is `None`.
- `AlphaDecisionEngine`: quality/opportunity/risk scores with explicit evidence coverage.
- `TradePlan`: bounded position cap, stop/targets, minimum 1.5R gate.
- Risk >= 75 vetoes otherwise strong opportunities.
- Low evidence coverage returns WAIT.

This sidecar-first deployment prevents a new scoring system from contaminating the V4 forecast history before shadow validation.

## Phase 2: Feature adapters and shadow mode

Create adapters from existing trusted pipeline artifacts only:

- technical trend / price structure
- SPY and QQQ relative strength
- RVOL / volume confirmation
- SEC/fundamental quality
- evidence-backed catalysts
- market regime / breadth
- realized volatility / ATR
- earnings-event risk
- provider/data quality

Run Alpha Engine in **shadow mode** for at least one maturity window. Persist decisions but do not change notifications or execution advice.

Acceptance gates:

- no increase in pipeline failure rate;
- deterministic replay produces identical scores for identical snapshots;
- missing-data tests pass;
- action rate is bounded (no signal spam);
- all actionable plans satisfy configured R:R and risk caps.

## Phase 3: Risk and portfolio engine

Portfolio gate operates after single-name alpha:

- max position by market regime;
- portfolio gross/net exposure;
- sector and factor concentration;
- pairwise/cross-position correlation;
- earnings/event concentration;
- drawdown-based de-risking;
- liquidity constraints.

Final size is `min(alpha_cap, market_cap, portfolio_cap, liquidity_cap)`.

## Phase 4: Outcome learning

Persist immutable feature snapshots and mature them at 5D/10D/20D horizons. Evaluate:

- directional hit rate;
- Brier score / calibration curve;
- expected-return MAE;
- excess return vs SPY/QQQ;
- max adverse/favorable excursion;
- realized R multiple;
- performance by regime and signal bucket.

Calibration may change probabilities only after sufficient samples. Structural weights are promoted through versioned offline evaluation, never adjusted ad hoc after one trade.

## Phase 5: Trader-facing Alpha Terminal

A trader should see, in this order:

1. Market regime and portfolio risk budget.
2. Ranked opportunities with evidence coverage.
3. Decision state: BUY_SETUP / WATCH / WAIT / AVOID.
4. Entry zone, invalidation, targets, R:R, max position.
5. What must happen before entry.
6. What would invalidate the thesis.
7. Forecast vs execution distinction.
8. Historical calibration for this signal bucket/regime.

The terminal should optimize for decision quality and restraint, not the number of indicators shown.
