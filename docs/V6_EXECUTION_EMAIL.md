# V6 Investor Execution Email

The final U.S. stock decision email is an **execution-oriented view** of the full V4 + V6 report. It does not replace the full Markdown/JSON artifacts and does not change any forecast, score, weight, threshold, database state, notification routing or trading behavior.

Its purpose is to let a reader answer four questions quickly:

1. What should I watch today?
2. Are 5D / 10D / 20D directions aligned?
3. If a deterministic trade plan exists, what are the entry, invalidation/stop, targets and maximum position?
4. What conditions would invalidate or confirm the setup?

## Execution hierarchy

The investor email uses the following hierarchy:

```text
Today action
    ↓
5D / 10D / 20D deterministic forecasts
    ↓
Deterministic V6 risk-control trade plan
    ↓
V4 research narrative and explanatory context
```

When a stock card contains a deterministic V6 entry range, that deterministic plan is the **only executable price source** shown in the investor email. Legacy V4 price and position references remain available in the full raw report for audit, but they are hidden from the inbox so the same card cannot show conflicting entry/stop/target instructions.

If no deterministic V6 entry range exists, the V4-derived plan is kept only as an explicitly labelled **auxiliary / non-execution** reference. A neutral/waiting setup therefore cannot accidentally look like an active trade instruction.

## Uncalibrated probability

`模型上行概率 ...（未校准）` is not shown in the investor email.

The value remains available in the full report where applicable, but the inbox must not present it as if it were an empirically calibrated win probability. Historical calibration and V6.4 research governance remain separate from the live execution view.

## Evidence labels

Two coverage concepts are made explicit:

- horizon cells use `因子覆盖`;
- the stock detail line uses `总体证据覆盖`.

Neither is a win rate or probability. The multi-horizon introduction states this directly.

## U.S. market presentation

The U.S. investor email normalizes presentation-only terms:

- numeric price references written as `123.45元` become `$123.45`;
- `EST`, `EDT` and `美东时间` are normalized to `ET（美东）` without changing the supplied clock time;
- obvious A-share turnover template text such as `...亿级别成交额` is removed from the inbox.

The raw report is not rewritten by these presentation rules.

## No-chase guard

If the final execution guard says `禁止追高` / `禁止追价`, explanatory confirmation lines in the same stock card cannot simultaneously say `可追`.

A breakout condition is instead rendered as a **strong confirmation only, not a chase instruction**. This keeps one execution policy inside a card while preserving the information that a breakout would be technically meaningful.

## Audit and safety boundary

The email transformation happens only on the final investor-facing Markdown before email HTML rendering. The full report and structured artifacts retain the richer source material for research and debugging.

This optimization does **not**:

- change Champion or Challenger definitions;
- change 5D / 10D / 20D bullish/bearish thresholds;
- change opportunity, quality or risk scores;
- change V4 research generation;
- change Alpha / Accuracy Lab or V6.4 governance;
- enable automatic promotion, automatic weight tuning or automatic trading;
- change Gmail recipients or notification routing.

The investor email is therefore a presentation and execution-consistency layer, not a new model.