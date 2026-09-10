from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.us_open_research_ledger import connect


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _avg(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _load_bars(
    conn: sqlite3.Connection, *, symbol: str, session_date: str
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT bar_time,open,high,low,close,volume
            FROM us_intraday_bars
            WHERE symbol=? AND session_date=? AND interval='1m'
            ORDER BY bar_time
            """,
            (symbol.strip().upper(), session_date),
        ).fetchall()
    )


def _execution_levels(packet: Mapping[str, Any]) -> tuple[float | None, float | None]:
    execution = packet.get("execution") if isinstance(packet.get("execution"), Mapping) else {}
    stop = _finite(execution.get("stop_loss"))
    targets = [
        value
        for value in (_finite(item) for item in (execution.get("targets") or ()))
        if value is not None and value > 0
    ]
    return stop, targets[0] if targets else None


def _roundtrip_return_pct(
    *, entry: float, exit_price: float, fee_bps: float
) -> float:
    gross = (exit_price / entry - 1.0) * 100.0
    # fee_bps is charged on both entry and exit notionals.
    return gross - 2.0 * max(0.0, fee_bps) / 100.0


def _simulate_after_fill(
    bars: Sequence[sqlite3.Row],
    *,
    fill_index: int,
    raw_entry_price: float,
    stop: float | None,
    target: float | None,
    slippage_bps: float,
    fee_bps: float,
) -> dict[str, Any]:
    slip = max(0.0, float(slippage_bps)) / 10_000.0
    entry = raw_entry_price * (1.0 + slip)
    if entry <= 0:
        raise ValueError("entry must be positive")

    first_touch = "close"
    ambiguous = False
    exit_raw = _finite(bars[-1]["close"]) if bars else None
    exit_index = len(bars) - 1
    for index in range(max(0, fill_index), len(bars)):
        row = bars[index]
        low = _finite(row["low"])
        high = _finite(row["high"])
        stop_here = bool(stop is not None and low is not None and low <= stop)
        target_here = bool(target is not None and high is not None and high >= target)
        if stop_here and target_here:
            # 1m OHLC does not reveal event ordering. Promotion statistics use
            # the adverse interpretation instead of cherry-picking the target.
            ambiguous = True
            first_touch = "ambiguous_stop_target_same_bar_conservative_stop"
            exit_raw = stop
            exit_index = index
            break
        if stop_here:
            first_touch = "stop"
            exit_raw = stop
            exit_index = index
            break
        if target_here:
            first_touch = "target1"
            exit_raw = target
            exit_index = index
            break

    if exit_raw is None or exit_raw <= 0:
        return {
            "return_pct": None,
            "first_touch": "missing_exit",
            "ambiguous": ambiguous,
            "exit_index": exit_index,
        }
    exit_price = exit_raw * (1.0 - slip)
    return {
        "return_pct": _roundtrip_return_pct(
            entry=entry,
            exit_price=exit_price,
            fee_bps=fee_bps,
        ),
        "first_touch": first_touch,
        "ambiguous": ambiguous,
        "entry_price_after_slippage": entry,
        "exit_price_after_slippage": exit_price,
        "exit_index": exit_index,
    }


def _simulate_row(
    row: Mapping[str, Any],
    bars: Sequence[sqlite3.Row],
    *,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any] | None:
    signal_time = _parse_dt(row.get("signal_bar_time"))
    signal_price = _finite(row.get("signal_price"))
    if signal_time is None or signal_price is None or signal_price <= 0 or not bars:
        return None

    indexed: list[tuple[datetime, sqlite3.Row]] = []
    for raw in bars:
        stamp = _parse_dt(raw["bar_time"])
        if stamp is not None:
            indexed.append((stamp, raw))
    future = [(stamp, raw) for stamp, raw in indexed if stamp > signal_time]
    if not future:
        return None
    future_rows = [raw for _, raw in future]

    packet = _json_object(row.get("packet_json"))
    decision = _json_object(row.get("decision_json"))
    stop, target = _execution_levels(packet)

    immediate = _simulate_after_fill(
        future_rows,
        fill_index=0,
        raw_entry_price=signal_price,
        stop=stop,
        target=target,
        slippage_bps=slippage_bps,
        fee_bps=fee_bps,
    )
    immediate_return = _finite(immediate.get("return_pct"))
    if immediate_return is None:
        return None

    ideal = _finite(decision.get("ideal_entry_price"))
    wait_raw = _finite(decision.get("expected_wait_minutes"))
    if wait_raw is None:
        wait_raw = _finite(decision.get("recheck_minutes"))
    wait_minutes = int(max(1.0, min(120.0, wait_raw or 30.0)))
    wait_cutoff = signal_time + timedelta(minutes=wait_minutes)

    optimized_return = 0.0
    fill_index = None
    minutes_to_fill = None
    optimized_touch = "not_filled_cash"
    optimized_ambiguous = False
    if ideal is not None and ideal > 0:
        if ideal >= signal_price * 0.9995:
            # A limit at/above the observed signal price is effectively immediate.
            fill_index = 0
            raw_fill = signal_price
            minutes_to_fill = 0.0
        else:
            raw_fill = ideal
            for index, (stamp, bar) in enumerate(future):
                if stamp > wait_cutoff:
                    break
                low = _finite(bar["low"])
                if low is not None and low <= ideal:
                    fill_index = index
                    minutes_to_fill = max(
                        0.0, (stamp - signal_time).total_seconds() / 60.0
                    )
                    break
        if fill_index is not None:
            optimized = _simulate_after_fill(
                future_rows,
                fill_index=fill_index,
                raw_entry_price=raw_fill,
                stop=stop,
                target=target,
                slippage_bps=slippage_bps,
                fee_bps=fee_bps,
            )
            value = _finite(optimized.get("return_pct"))
            if value is None:
                return None
            optimized_return = value
            optimized_touch = str(optimized.get("first_touch") or "close")
            optimized_ambiguous = bool(optimized.get("ambiguous"))

    alpha = optimized_return - immediate_return
    return {
        "session_date": str(row.get("session_date") or ""),
        "symbol": str(row.get("symbol") or "").upper(),
        "market_regime": str(row.get("market_regime") or "unknown"),
        "filled": fill_index is not None,
        "minutes_to_fill": minutes_to_fill,
        "optimized_return_pct": optimized_return,
        "immediate_return_pct": immediate_return,
        "alpha_vs_immediate_pct": alpha,
        "no_fill_opportunity_cost_pct": (
            max(0.0, immediate_return) if fill_index is None else 0.0
        ),
        "optimized_first_touch": optimized_touch,
        "immediate_first_touch": immediate.get("first_touch"),
        "optimized_ambiguous": optimized_ambiguous,
        "immediate_ambiguous": bool(immediate.get("ambiguous")),
        "wait_minutes": wait_minutes,
    }


def _daily_portfolio_returns(
    items: Sequence[Mapping[str, Any]], field: str
) -> list[float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in items:
        value = _finite(item.get(field))
        if value is not None:
            grouped[str(item.get("session_date") or "")].append(value)
    return [mean(grouped[key]) for key in sorted(grouped) if grouped[key]]


def _max_drawdown_pct(returns_pct: Sequence[float]) -> float | None:
    if not returns_pct:
        return None
    equity = 1.0
    high = 1.0
    max_dd = 0.0
    for value in returns_pct:
        equity *= max(0.0, 1.0 + value / 100.0)
        high = max(high, equity)
        if high > 0:
            max_dd = max(max_dd, (high - equity) / high * 100.0)
    return max_dd


def _cluster_bootstrap_ci(
    items: Sequence[Mapping[str, Any]],
    *,
    field: str,
    iterations: int,
    seed: int = 20260910,
) -> tuple[float | None, float | None]:
    clusters: dict[str, list[float]] = defaultdict(list)
    for item in items:
        value = _finite(item.get(field))
        if value is not None:
            clusters[str(item.get("session_date") or "")].append(value)
    dates = sorted(key for key, values in clusters.items() if values)
    if len(dates) < 2 or iterations <= 0:
        return None, None
    rng = random.Random(seed)
    sampled_means: list[float] = []
    for _ in range(iterations):
        values: list[float] = []
        for _ in dates:
            chosen = rng.choice(dates)
            values.extend(clusters[chosen])
        if values:
            sampled_means.append(mean(values))
    if not sampled_means:
        return None, None
    sampled_means.sort()
    low_index = max(0, int(0.025 * (len(sampled_means) - 1)))
    high_index = min(
        len(sampled_means) - 1,
        int(0.975 * (len(sampled_means) - 1)),
    )
    return sampled_means[low_index], sampled_means[high_index]


def _metrics(
    items: Sequence[Mapping[str, Any]], *, bootstrap_iterations: int
) -> dict[str, Any]:
    optimized = [
        float(item["optimized_return_pct"])
        for item in items
        if _finite(item.get("optimized_return_pct")) is not None
    ]
    immediate = [
        float(item["immediate_return_pct"])
        for item in items
        if _finite(item.get("immediate_return_pct")) is not None
    ]
    alpha = [
        float(item["alpha_vs_immediate_pct"])
        for item in items
        if _finite(item.get("alpha_vs_immediate_pct")) is not None
    ]
    fills = [item for item in items if bool(item.get("filled"))]
    minutes = [
        float(item["minutes_to_fill"])
        for item in fills
        if _finite(item.get("minutes_to_fill")) is not None
    ]
    bootstrap_low, bootstrap_high = _cluster_bootstrap_ci(
        items,
        field="alpha_vs_immediate_pct",
        iterations=bootstrap_iterations,
    )
    optimized_daily = _daily_portfolio_returns(items, "optimized_return_pct")
    immediate_daily = _daily_portfolio_returns(items, "immediate_return_pct")
    return {
        "samples": len(items),
        "session_dates": len({str(item.get("session_date") or "") for item in items}),
        "symbols": len({str(item.get("symbol") or "") for item in items}),
        "fills": len(fills),
        "fill_rate": len(fills) / len(items) if items else None,
        "optimized_avg_modeled_return_pct": _avg(optimized),
        "immediate_avg_modeled_return_pct": _avg(immediate),
        "avg_alpha_vs_immediate_pct": _avg(alpha),
        "median_alpha_vs_immediate_pct": median(alpha) if alpha else None,
        "bootstrap_95pct_alpha_low": bootstrap_low,
        "bootstrap_95pct_alpha_high": bootstrap_high,
        "optimized_max_drawdown_pct": _max_drawdown_pct(optimized_daily),
        "immediate_max_drawdown_pct": _max_drawdown_pct(immediate_daily),
        "avg_minutes_to_fill": _avg(minutes),
        "no_fill_opportunity_cost_avg_pct": _avg(
            [float(item.get("no_fill_opportunity_cost_pct") or 0.0) for item in items]
        ),
        "ambiguous_same_bar_count": sum(
            bool(item.get("optimized_ambiguous")) or bool(item.get("immediate_ambiguous"))
            for item in items
        ),
    }


def run(
    db_path: str | Path,
    *,
    fee_bps: float = 2.0,
    slippage_bps: float = 3.0,
    bootstrap_iterations: int = 1000,
    minimum_samples: int = 60,
    minimum_session_dates: int = 20,
    minimum_symbols: int = 3,
    minimum_fills: int = 20,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(us_open_signals)")}
        required = {
            "session_date",
            "symbol",
            "market_regime",
            "signal_bar_time",
            "signal_price",
            "packet_json",
            "decision_json",
            "settled_at",
        }
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"entry-optimizer ledger columns missing: {missing}")
        rows = conn.execute(
            """
            SELECT id,session_date,symbol,market_regime,signal_bar_time,signal_price,
                   packet_json,decision_json
            FROM us_open_signals
            WHERE settled_at IS NOT NULL
            ORDER BY session_date,symbol,id
            """
        ).fetchall()
        observations: list[dict[str, Any]] = []
        missing_bars = 0
        for raw in rows:
            row = dict(raw)
            bars = _load_bars(
                conn,
                symbol=str(row["symbol"]),
                session_date=str(row["session_date"]),
            )
            outcome = _simulate_row(
                row,
                bars,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            if outcome is None:
                missing_bars += 1
                continue
            observations.append(outcome)
    finally:
        conn.close()

    overall = _metrics(observations, bootstrap_iterations=bootstrap_iterations)
    regimes = sorted({str(item.get("market_regime") or "unknown") for item in observations})
    by_regime = {
        regime: _metrics(
            [item for item in observations if str(item.get("market_regime") or "unknown") == regime],
            bootstrap_iterations=max(200, bootstrap_iterations // 2),
        )
        for regime in regimes
    }

    optimized_dd = _finite(overall.get("optimized_max_drawdown_pct"))
    immediate_dd = _finite(overall.get("immediate_max_drawdown_pct"))
    drawdown_ok = bool(
        optimized_dd is not None
        and immediate_dd is not None
        and optimized_dd <= immediate_dd + 2.0
    )
    gates = {
        "minimum_samples": overall["samples"] >= minimum_samples,
        "minimum_session_dates": overall["session_dates"] >= minimum_session_dates,
        "minimum_symbols": overall["symbols"] >= minimum_symbols,
        "minimum_fills": overall["fills"] >= minimum_fills,
        "positive_mean_alpha": bool((overall["avg_alpha_vs_immediate_pct"] or 0.0) > 0.0),
        "nonnegative_median_alpha": bool((overall["median_alpha_vs_immediate_pct"] or -1e9) >= 0.0),
        "cluster_bootstrap_lower_bound_nonnegative": bool(
            overall["bootstrap_95pct_alpha_low"] is not None
            and overall["bootstrap_95pct_alpha_low"] >= 0.0
        ),
        "drawdown_not_materially_worse": drawdown_ok,
    }
    eligible = all(gates.values())
    return {
        "version": "entry-optimizer-backtest-v2-execution-path",
        "definition": (
            "compare immediate entry with ideal limit entry using 1m first-touch stop/target paths; "
            "same-bar stop+target is conservatively treated as stop; unfilled optimized orders stay cash"
        ),
        "execution_assumptions": {
            "fee_bps_each_side": max(0.0, fee_bps),
            "slippage_bps_each_side": max(0.0, slippage_bps),
            "same_bar_ambiguity": "conservative_stop",
            "unfilled_limit": "cash",
            "bootstrap_cluster": "session_date",
            "bootstrap_iterations": bootstrap_iterations,
        },
        "observations": len(observations),
        "missing_or_unusable_intraday_paths": missing_bars,
        "overall": overall,
        "by_regime": by_regime,
        "promotion_check": {
            "minimum_samples_required": minimum_samples,
            "minimum_session_dates_required": minimum_session_dates,
            "minimum_symbols_required": minimum_symbols,
            "minimum_fills_required": minimum_fills,
            "gates": gates,
            "eligible": eligible,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execution-path backtest for optimized U.S. opening entries"
    )
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--output", default="open_confirmation_reports/entry_optimizer_backtest.json"
    )
    parser.add_argument("--fee-bps", type=float, default=2.0)
    parser.add_argument("--slippage-bps", type=float, default=3.0)
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--minimum-samples", type=int, default=60)
    parser.add_argument("--minimum-session-dates", type=int, default=20)
    parser.add_argument("--minimum-symbols", type=int, default=3)
    parser.add_argument("--minimum-fills", type=int, default=20)
    # Backward-compatible argument. Costs are now explicitly modeled rather than
    # subtracted as a flat post-hoc hurdle.
    parser.add_argument("--cost-hurdle-pct", type=float, default=None)
    args = parser.parse_args()
    payload = run(
        args.db,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        bootstrap_iterations=max(100, args.bootstrap_iterations),
        minimum_samples=max(1, args.minimum_samples),
        minimum_session_dates=max(1, args.minimum_session_dates),
        minimum_symbols=max(1, args.minimum_symbols),
        minimum_fills=max(1, args.minimum_fills),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
