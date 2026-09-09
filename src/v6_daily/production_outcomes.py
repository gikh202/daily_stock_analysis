from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .production_common import DEFAULT_HORIZONS, finite, parse_date


def _future_bars(
    stock_db_path: str,
    *,
    code: str,
    analysis_date: str,
    needed: int,
) -> list[Dict[str, Any]]:
    conn = sqlite3.connect(f"file:{Path(stock_db_path)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT date, high, low, close FROM stock_daily "
            "WHERE code=? AND date>? ORDER BY date ASC LIMIT ?",
            (str(code), analysis_date, max(1, int(needed))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _benchmark_return(
    stock_db_path: str,
    *,
    code: str,
    analysis_date: str,
    horizon: int,
) -> Optional[float]:
    conn = sqlite3.connect(f"file:{Path(stock_db_path)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        start_row = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (code, analysis_date),
        ).fetchone()
        future = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date>? ORDER BY date ASC LIMIT ?",
            (code, analysis_date, int(horizon)),
        ).fetchall()
    finally:
        conn.close()
    if start_row is None or len(future) < horizon:
        return None
    start = finite(start_row["close"])
    end = finite(future[-1]["close"])
    if start is None or end is None or start <= 0:
        return None
    return round((end / start - 1.0) * 100.0, 6)


def backfill_missing_benchmark_outcomes(
    store: Any,
    stock_db_path: str,
) -> Dict[str, int]:
    """Repair benchmark/alpha fields on already-mature normalized outcomes.

    The benchmark source is expected to be a read-only settlement database that
    contains SPY/QQQ daily bars. Existing forecast returns are never recomputed;
    only missing benchmark-return and excess-return fields are filled.
    """
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                o.id,
                o.horizon_days,
                o.return_pct,
                o.benchmark_spy_return_pct,
                o.benchmark_qqq_return_pct,
                o.excess_vs_spy_pct,
                o.excess_vs_qqq_pct,
                f.effective_trade_date
            FROM v6_forecast_outcomes o
            JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
            WHERE (
                o.benchmark_spy_return_pct IS NULL
                OR o.benchmark_qqq_return_pct IS NULL
                OR o.excess_vs_spy_pct IS NULL
                OR o.excess_vs_qqq_pct IS NULL
            )
              AND o.return_pct IS NOT NULL
              AND date(f.effective_trade_date) IS NOT NULL
            ORDER BY o.id
            """
        ).fetchall()

    cache: dict[tuple[str, str, int], Optional[float]] = {}

    def benchmark(code: str, analysis_date: str, horizon: int) -> Optional[float]:
        key = (str(code).upper(), analysis_date, int(horizon))
        if key not in cache:
            cache[key] = _benchmark_return(
                stock_db_path,
                code=key[0],
                analysis_date=analysis_date,
                horizon=int(horizon),
            )
        return cache[key]

    candidates = len(rows)
    repaired_spy = 0
    repaired_qqq = 0
    repaired_rows = 0
    for row in rows:
        outcome_id = int(row["id"])
        horizon = int(row["horizon_days"])
        analysis_date = parse_date(row["effective_trade_date"])
        realized = finite(row["return_pct"])
        if analysis_date is None or realized is None:
            continue

        current_spy = finite(row["benchmark_spy_return_pct"])
        current_qqq = finite(row["benchmark_qqq_return_pct"])
        current_excess_spy = finite(row["excess_vs_spy_pct"])
        current_excess_qqq = finite(row["excess_vs_qqq_pct"])

        spy = current_spy
        qqq = current_qqq
        if spy is None:
            spy = benchmark("SPY", analysis_date, horizon)
        if qqq is None:
            qqq = benchmark("QQQ", analysis_date, horizon)

        excess_spy = (
            current_excess_spy
            if current_excess_spy is not None
            else (
                None
                if spy is None
                else round(realized - float(spy), 6)
            )
        )
        excess_qqq = (
            current_excess_qqq
            if current_excess_qqq is not None
            else (
                None
                if qqq is None
                else round(realized - float(qqq), 6)
            )
        )

        changed = False
        if current_spy is None and spy is not None:
            repaired_spy += 1
            changed = True
        if current_qqq is None and qqq is not None:
            repaired_qqq += 1
            changed = True
        if current_excess_spy is None and excess_spy is not None:
            changed = True
        if current_excess_qqq is None and excess_qqq is not None:
            changed = True
        if not changed:
            continue

        with store.connect() as conn:
            conn.execute(
                """
                UPDATE v6_forecast_outcomes
                SET benchmark_spy_return_pct=COALESCE(benchmark_spy_return_pct, ?),
                    benchmark_qqq_return_pct=COALESCE(benchmark_qqq_return_pct, ?),
                    excess_vs_spy_pct=COALESCE(excess_vs_spy_pct, ?),
                    excess_vs_qqq_pct=COALESCE(excess_vs_qqq_pct, ?)
                WHERE id=?
                """,
                (
                    finite(spy),
                    finite(qqq),
                    finite(excess_spy),
                    finite(excess_qqq),
                    outcome_id,
                ),
            )
            conn.commit()
        repaired_rows += 1

    with store.connect() as conn:
        missing = conn.execute(
            """
            SELECT
                SUM(CASE WHEN benchmark_spy_return_pct IS NULL
                          OR excess_vs_spy_pct IS NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN benchmark_qqq_return_pct IS NULL
                          OR excess_vs_qqq_pct IS NULL THEN 1 ELSE 0 END)
            FROM v6_forecast_outcomes
            """
        ).fetchone()

    return {
        "candidates": candidates,
        "repaired_rows": repaired_rows,
        "repaired_spy": repaired_spy,
        "repaired_qqq": repaired_qqq,
        "still_missing_spy": int((missing[0] if missing else 0) or 0),
        "still_missing_qqq": int((missing[1] if missing else 0) or 0),
        "benchmark_queries": len(cache),
    }


def mature_normalized_outcomes(
    store: Any,
    stock_db_path: str,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    neutral_band_pct: float = 2.0,
) -> Dict[str, int]:
    normalized = sorted({int(value) for value in horizons if int(value) > 0})
    if not normalized:
        return {"evaluated": 0, "not_yet_mature": 0}

    evaluated = 0
    pending = 0
    max_horizon = max(normalized)
    for signal in store.all_signals():
        done = store.evaluated_horizons(int(signal["id"]))
        needed = [h for h in normalized if h not in done]
        if not needed:
            continue
        analysis_date = parse_date(signal["effective_trade_date"]) or parse_date(
            signal["analysis_created_at"]
        )
        if analysis_date is None:
            pending += len(needed)
            continue
        bars = _future_bars(
            stock_db_path,
            code=str(signal["code"]),
            analysis_date=analysis_date,
            needed=max_horizon,
        )
        start = finite(signal["baseline_price"])
        if start is None or start <= 0:
            pending += len(needed)
            continue
        try:
            forecasts = json.loads(signal["horizon_forecasts_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            forecasts = {}

        for horizon in needed:
            if len(bars) < horizon:
                pending += 1
                continue
            window = bars[:horizon]
            end = finite(window[-1].get("close"))
            highs = [
                value
                for value in (finite(row.get("high")) for row in window)
                if value is not None
            ]
            lows = [
                value
                for value in (finite(row.get("low")) for row in window)
                if value is not None
            ]
            if end is None or end <= 0:
                pending += 1
                continue
            block = forecasts.get(f"{horizon}d") if isinstance(forecasts, dict) else None
            direction = str(
                block.get("direction")
                if isinstance(block, dict)
                else signal["direction"]
            )
            forecast_score = (
                finite(block.get("score"))
                if isinstance(block, dict)
                else finite(signal["forecast_score"])
            )
            spy_return = _benchmark_return(
                stock_db_path,
                code="SPY",
                analysis_date=analysis_date,
                horizon=horizon,
            )
            qqq_return = _benchmark_return(
                stock_db_path,
                code="QQQ",
                analysis_date=analysis_date,
                horizon=horizon,
            )
            if store.save_outcome(
                signal_id=int(signal["id"]),
                horizon_days=horizon,
                end_trade_date=str(window[-1].get("date") or ""),
                start_price=start,
                end_price=end,
                max_high=max(highs) if highs else None,
                min_low=min(lows) if lows else None,
                direction=direction,
                neutral_band_pct=neutral_band_pct,
                forecast_score=forecast_score,
                benchmark_spy_return_pct=spy_return,
                benchmark_qqq_return_pct=qqq_return,
            ):
                evaluated += 1

    return {"evaluated": evaluated, "not_yet_mature": pending}
