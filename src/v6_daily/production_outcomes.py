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
