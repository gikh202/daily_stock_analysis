from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from scripts.backtest_us_open_confirmation import (
    PlanCandidate,
    _latest_causal_candidate,
    _next_session_date,
    _opening_window,
    _regular_session,
    build_observation,
    fetch_history,
    load_candidates,
)
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2
from src.forecasting import IntradayTimingModel

NY = ZoneInfo("America/New_York")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _forecast_probabilities(db_path: str | Path) -> dict[tuple[str, str], dict[int, float]]:
    result: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.symbol, f.effective_trade_date, f.v6_created_at,
                   h.horizon_days, h.payload_json, h.score
            FROM v6_forecast_runs f
            JOIN v6_horizon_forecasts h ON h.forecast_run_id=f.id
            WHERE h.horizon_days IN (1,5)
              AND f.effective_trade_date IS NOT NULL
            ORDER BY f.v6_created_at, f.id, h.horizon_days
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        trade_date = str(row["effective_trade_date"] or "")[:10]
        horizon = int(row["horizon_days"])
        if not symbol or not trade_date:
            continue
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["payload_json"] or "{}"))
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        probability = _finite(payload.get("probability_up"))
        if probability is None:
            score = _finite(row["score"])
            probability = None if score is None else score / 100.0
        if probability is not None:
            result[(symbol, trade_date)][horizon] = max(0.02, min(0.98, probability))
    return dict(result)


def _intraday_features(opening: Any) -> dict[str, float | None]:
    volumes = opening["Volume"].fillna(0)
    typical = (opening["High"] + opening["Low"] + opening["Close"]) / 3.0
    volume_sum = float(volumes.sum())
    vwap = float((typical * volumes).sum() / volume_sum) if volume_sum > 0 else None
    closes = [float(value) for value in opening["Close"].dropna().tolist()]
    last5 = (
        (closes[-1] / closes[-6] - 1.0) * 100.0
        if len(closes) >= 6 and closes[-6] > 0
        else None
    )
    returns = [
        (closes[index] / closes[index - 1] - 1.0) * 100.0
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    vol = pstdev(returns) * math.sqrt(30.0) if len(returns) >= 3 else None
    return {
        "session_vwap": vwap,
        "last_5m_return_pct": last5,
        "intraday_volatility_pct": vol,
    }


def _max_drawdown(returns_by_date: Mapping[str, Sequence[float]]) -> float | None:
    if not returns_by_date:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for session_date in sorted(returns_by_date):
        values = [float(value) for value in returns_by_date[session_date]]
        if not values:
            continue
        daily_return = mean(values) / 100.0
        equity *= 1.0 + daily_return
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1.0)
    return max_dd * 100.0


def _stats(rows: Sequence[dict[str, Any]], prefix: str) -> dict[str, Any]:
    returns = [float(row[f"{prefix}_return_pct"]) for row in rows]
    mfes = [float(row[f"{prefix}_mfe_pct"]) for row in rows]
    maes = [float(row[f"{prefix}_mae_pct"]) for row in rows]
    by_date: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, returns):
        by_date[str(row["session_date"])].append(value)
    return {
        "avg_return_pct": mean(returns) if returns else None,
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "avg_mfe_pct": mean(mfes) if mfes else None,
        "avg_mae_pct": mean(maes) if maes else None,
        "max_drawdown_pct": _max_drawdown(by_date),
    }


def _probe_three_year_minute_source(symbol: str, start: date) -> dict[str, Any]:
    try:
        import yfinance as yf

        frame = yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(start + timedelta(days=5)).isoformat(),
            interval="1m",
            auto_adjust=False,
            prepost=False,
            actions=False,
        )
        rows = 0 if frame is None else int(len(frame))
        return {"provider": "yfinance", "success": rows > 0, "rows": rows}
    except Exception as exc:
        return {
            "provider": "yfinance",
            "success": False,
            "rows": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run(
    *,
    v6_db: str | Path,
    years: int = 3,
    transaction_cost_bps: float = 10.0,
) -> dict[str, Any]:
    today = date.today()
    requested_start = today - timedelta(days=365 * max(1, int(years)))
    candidates = [
        item
        for item in load_candidates(v6_db)
        if item.source == "final_fusion"
        and date.fromisoformat(item.effective_trade_date) >= requested_start
    ]
    probabilities = _forecast_probabilities(v6_db)

    if not candidates:
        return {
            "version": "wait-better-entry-backtest-v1",
            "requested_years": years,
            "requested_start_date": requested_start.isoformat(),
            "wait_samples": 0,
            "three_year_requirement_met": False,
            "decision": "keep_v7.1",
            "reason": "no final-fusion historical candidates in requested window",
        }

    provider_probe = _probe_three_year_minute_source(candidates[0].symbol, requested_start)

    # Yahoo's minute-history endpoint normally exposes only recent intraday data.
    # Keep the requested three-year audit explicit, then run the largest causal
    # recent window the production dependency can actually return.
    recent_start = max(requested_start, today - timedelta(days=29))
    recent_candidates = [
        item for item in candidates if date.fromisoformat(item.effective_trade_date) >= recent_start
    ]
    grouped: dict[str, list[PlanCandidate]] = defaultdict(list)
    for candidate in recent_candidates:
        grouped[candidate.symbol].append(candidate)

    wait_rows: list[dict[str, Any]] = []
    observed_dates: set[str] = set()
    data_errors: list[str] = []

    for symbol, symbol_candidates in sorted(grouped.items()):
        try:
            frame = fetch_history(symbol, recent_start - timedelta(days=8), today + timedelta(days=1))
        except Exception as exc:
            data_errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            continue

        by_plan_date: dict[str, list[PlanCandidate]] = defaultdict(list)
        for candidate in symbol_candidates:
            by_plan_date[candidate.effective_trade_date].append(candidate)

        for plan_date_text, group in sorted(by_plan_date.items()):
            plan_date = date.fromisoformat(plan_date_text)
            session_date = _next_session_date(frame, plan_date)
            if session_date is None:
                continue
            candidate = _latest_causal_candidate(group, session_date=session_date)
            if candidate is None:
                continue
            observation = build_observation(candidate, frame, session_date)
            if observation is None:
                continue

            opening = _opening_window(frame, session_date)
            session = _regular_session(frame, session_date)
            if opening.empty or session.empty:
                continue
            signal_time = opening.index[-1]
            features = _intraday_features(opening)
            evaluated_at = (signal_time + timedelta(minutes=1)).to_pydatetime()
            base = classify_confirmation_v2(
                candidate.packet,
                observation.snapshot,
                evaluated_at=evaluated_at,
                data_error=None,
            )
            forecast = probabilities.get((symbol, plan_date_text), {})
            timing = IntradayTimingModel().assess(
                base_status=base.status,
                current_price=observation.snapshot.current_price,
                entry_low=base.entry_low,
                entry_high=base.entry_high,
                stop_loss=base.stop_loss,
                session_low=observation.snapshot.session_low,
                session_high=observation.snapshot.session_high,
                session_vwap=_finite(features["session_vwap"]),
                last_5m_return_pct=_finite(features["last_5m_return_pct"]),
                intraday_volatility_pct=_finite(features["intraday_volatility_pct"]),
                minutes_since_open=14,
                probability_up_1d=forecast.get(1),
                probability_up_5d=forecast.get(5),
            )
            if timing.action != "WAIT_BETTER_ENTRY":
                continue
            target = _finite(timing.expected_better_price)
            signal_price = float(observation.snapshot.current_price)
            if target is None or not (0 < target < signal_price):
                continue

            wait_cutoff = signal_time + timedelta(minutes=30)
            wait_future = session[(session.index > signal_time) & (session.index <= wait_cutoff)]
            matching = wait_future[wait_future["Low"] <= target]
            hit = not matching.empty
            if hit:
                entry_time = matching.index[0]
                wait_minutes = max(0.0, (entry_time - signal_time).total_seconds() / 60.0)
                post_entry = session[session.index >= entry_time]
                close_price = float(session.iloc[-1]["Close"])
                wait_return = (close_price / target - 1.0) * 100.0
                wait_mfe = (float(post_entry["High"].max()) / target - 1.0) * 100.0
                wait_mae = (float(post_entry["Low"].min()) / target - 1.0) * 100.0
                improvement = (signal_price - target) / signal_price * 100.0
            else:
                # A limit that never trades leaves capital in cash for this signal.
                wait_minutes = 30.0
                wait_return = 0.0
                wait_mfe = 0.0
                wait_mae = 0.0
                improvement = 0.0

            observed_dates.add(session_date.isoformat())
            wait_rows.append(
                {
                    "symbol": symbol,
                    "plan_date": plan_date_text,
                    "session_date": session_date.isoformat(),
                    "signal_price": signal_price,
                    "expected_better_price": target,
                    "better_entry_hit": hit,
                    "price_improvement_pct": improvement,
                    "wait_minutes": wait_minutes,
                    "immediate_return_pct": observation.close_return_pct,
                    "immediate_mfe_pct": observation.mfe_pct,
                    "immediate_mae_pct": observation.mae_pct,
                    "wait_return_pct": wait_return,
                    "wait_mfe_pct": wait_mfe,
                    "wait_mae_pct": wait_mae,
                }
            )

    hit_rows = [row for row in wait_rows if row["better_entry_hit"]]
    hit_rate = (
        sum(bool(row["better_entry_hit"]) for row in wait_rows) / len(wait_rows)
        if wait_rows
        else None
    )
    avg_improvement = (
        mean(float(row["price_improvement_pct"]) for row in wait_rows)
        if wait_rows
        else None
    )
    avg_improvement_on_fill = (
        mean(float(row["price_improvement_pct"]) for row in hit_rows)
        if hit_rows
        else None
    )
    avg_wait = (
        mean(float(row["wait_minutes"]) for row in wait_rows) if wait_rows else None
    )
    immediate = _stats(wait_rows, "immediate") if wait_rows else {}
    wait = _stats(wait_rows, "wait") if wait_rows else {}
    alpha = None
    if immediate.get("avg_return_pct") is not None and wait.get("avg_return_pct") is not None:
        alpha = float(wait["avg_return_pct"]) - float(immediate["avg_return_pct"])

    observed_start = min(observed_dates) if observed_dates else None
    observed_end = max(observed_dates) if observed_dates else None
    requested_span_days = max(1, (today - requested_start).days)
    observed_span_days = (
        (date.fromisoformat(observed_end) - date.fromisoformat(observed_start)).days
        if observed_start and observed_end
        else 0
    )
    three_year_requirement_met = bool(
        provider_probe.get("success")
        and observed_start
        and date.fromisoformat(observed_start) <= requested_start + timedelta(days=7)
        and observed_span_days >= requested_span_days - 14
    )
    transaction_cost_pct = float(transaction_cost_bps) / 100.0
    promotion_checks = {
        "three_year_intraday_coverage": three_year_requirement_met,
        "hit_rate_gt_50pct": hit_rate is not None and hit_rate > 0.50,
        "avg_price_improvement_gt_trading_cost": avg_improvement is not None and avg_improvement > transaction_cost_pct,
        "wait_alpha_gt_immediate": alpha is not None and alpha > 0.0,
    }
    promote = all(promotion_checks.values())

    return {
        "version": "wait-better-entry-backtest-v1",
        "generated_at": datetime.now(NY).isoformat(),
        "requested_years": int(years),
        "requested_start_date": requested_start.isoformat(),
        "transaction_cost_bps": float(transaction_cost_bps),
        "minute_history_provider_probe": provider_probe,
        "candidate_count_requested_window": len(candidates),
        "candidate_count_recent_provider_window": len(recent_candidates),
        "observed_start_date": observed_start,
        "observed_end_date": observed_end,
        "observed_session_count": len(observed_dates),
        "three_year_requirement_met": three_year_requirement_met,
        "data_errors": data_errors[:20],
        "wait_samples": len(wait_rows),
        "better_entry_hit_rate": hit_rate,
        "avg_price_improvement_pct": avg_improvement,
        "avg_price_improvement_on_fill_pct": avg_improvement_on_fill,
        "avg_wait_minutes": avg_wait,
        "immediate_buy": immediate,
        "wait_better_entry": wait,
        "alpha_wait_vs_immediate_pct": alpha,
        "promotion_checks": promotion_checks,
        "decision": "v7.2_candidate" if promote else "keep_v7.1",
        "rows": wait_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Causal WAIT_BETTER_ENTRY versus immediate-buy benchmark")
    parser.add_argument("--v6-db", required=True)
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    parser.add_argument("--output", default="wait_better_entry_backtest.json")
    args = parser.parse_args()
    result = run(
        v6_db=args.v6_db,
        years=args.years,
        transaction_cost_bps=args.transaction_cost_bps,
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
