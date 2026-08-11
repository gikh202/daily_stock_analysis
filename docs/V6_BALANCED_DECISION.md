# V6 Balanced Buy Decision Analysis

The investor-facing daily report separates **analysis** from **execution**.

A conservative final action must not erase a valid bullish thesis, and a bullish setup must not erase material risk evidence. The report therefore renders both sides before stating what can actually be done now.

## Decision structure

Each stock with structured V4 research gets a `是否值得买` block containing:

1. a current verdict;
2. `支持买入的证据`;
3. `支持等待/不买的证据`;
4. `关键分界`.

The verdict is an execution interpretation, not a replacement for the underlying evidence.

### Verdict meanings

- `可以买，但只按计划买`: the setup has reached buy preparation; entry, stop and maximum position remain mandatory.
- `条件式可买`: the thesis is sufficiently constructive to keep a live deterministic plan, but the current action is still WATCH; execution requires the entry zone and confirmation conditions.
- `继续观察`: potential opportunity exists, but no currently executable setup is established.
- `暂不买，等待确认`: bullish evidence may still exist, but V4/V6 alignment or the trade plan is not strong enough to act now.
- `当前不买/回避`: the risk gate currently dominates execution; any remaining bullish evidence is still shown for audit and future reassessment.

## Evidence symmetry

The analytical layer is deliberately symmetric:

- WAIT and AVOID do not delete bullish evidence.
- BUY_SETUP and bullish forecasts do not delete bearish evidence.
- Missing evidence remains missing rather than being invented to make both sides look equally populated.
- The original V4 `analysis_summary` remains visible as `投研摘要（原始观点）`; it may contain an earlier buy/position opinion, but it is not the final execution authority.

Structured bullish evidence is drawn from the strongest bullish signal, earnings outlook, positive catalysts and bullish forecast rationale. Structured waiting/risk evidence is drawn from the strongest bearish signal, risk alerts, V6 risks, risk warnings and bearish forecast rationale.

## Execution authority

This feature does not change scoring or trading behavior.

Execution remains governed by:

- the final V6 decision/action;
- the active deterministic entry zone;
- stop/invalidation levels;
- maximum position limits;
- portfolio/exposure/drawdown gates;
- next confirmation conditions.

An active deterministic plan still requires both a real entry zone and a positive maximum-position allowance. A WATCH verdict described as `条件式可买` is therefore not an instruction to buy immediately.

## Safety boundary

This change does not modify:

- V6 opportunity, quality or risk scores;
- 5D / 10D / 20D direction thresholds;
- Champion/Challenger weights;
- PortfolioGate decisions;
- automatic promotion or weight tuning;
- notification routing;
- automatic trading behavior.

It changes only how the existing evidence and execution state are reconciled and explained to the investor.