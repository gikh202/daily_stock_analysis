from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from src.alpha_engine.models import AlphaFeatures

from .accuracy import (
    ETF_HORIZON_WEIGHTS,
    HORIZONS,
    STOCK_HORIZON_WEIGHTS,
    _direction,
    _weighted_score,
)


ACCURACY_LAB_VERSION = "v6.2-accuracy-lab.1"
DEFAULT_COST_BPS = 10.0
DEFAULT_MAX_HOLDING_BARS = 20
DEFAULT_PROMOTION_MIN_SAMPLES = 100

# Research-only challengers. They never modify the production champion forecast.
# Each variant perturbs the current deterministic evidence mix and is evaluated
# in shadow mode on exactly the same future bars as the champion.
SHADOW_VARIANT_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "trend_guard": {
        "trend": 1.35,
        "momentum": 0.82,
        "relative_strength": 0.95,
        "sector_relative_strength": 0.95,
        "volume_confirmation": 0.90,
    },
    "momentum_focus": {
        "trend": 0.88,
        "momentum": 1.35,
        "relative_strength": 1.00,
        "volume_confirmation": 1.15,
        "market_regime": 0.92,
    },
    "relative_strength_focus": {
        "trend": 0.90,
        "momentum": 0.90,
        "relative_strength": 1.35,
        "sector_relative_strength": 1.25,
        "volume_confirmation": 0.95,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean(values: Iterable[Any]) -> Optional[float]:
    clean = [value for value in (_finite(item) for item in values) if value is not None]
    return None if not clean else statistics.fmean(clean)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[Optional[float], Optional[float]]:
    """95% Wilson interval for a binomial hit rate, returned as percentages."""
    n = int(total)
    if n <= 0:
        return None, None
    k = max(0, min(n, int(successes)))
    p = k / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    half = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denominator
    return round(100.0 * max(0.0, center - half), 2), round(100.0 * min(1.0, center + half), 2)


def _profile_with_multipliers(
    base: Mapping[int, Mapping[str, float]],
    multipliers: Mapping[str, float],
) -> Dict[int, Dict[str, float]]:
    result: Dict[int, Dict[str, float]] = {}
    for horizon, weights in base.items():
        adjusted = {
            name: max(0.0, float(weight)) * max(0.0, float(multipliers.get(name, 1.0)))
            for name, weight in weights.items()
        }
        total = sum(adjusted.values())
        if total <= 0:
            result[int(horizon)] = dict(weights)
        else:
            result[int(horizon)] = {
                name: round(value / total, 8) for name, value in adjusted.items()
            }
    return result


def shadow_profiles(instrument_type: str) -> Dict[str, Dict[int, Dict[str, float]]]:
    base = ETF_HORIZON_WEIGHTS if str(instrument_type or "").upper() == "ETF" else STOCK_HORIZON_WEIGHTS
    return {
        name: _profile_with_multipliers(base, multipliers)
        for name, multipliers in SHADOW_VARIANT_MULTIPLIERS.items()
    }


def build_shadow_forecasts(features: AlphaFeatures, *, instrument_type: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for variant, profile in shadow_profiles(instrument_type).items():
        blocks: Dict[str, Dict[str, Any]] = {}
        for horizon in HORIZONS:
            weights = profile[int(horizon)]
            score, coverage = _weighted_score(features, weights)
            blocks[f"{int(horizon)}d"] = {
                "horizon_days": int(horizon),
                "score": score,
                "direction": _direction(score, coverage, int(horizon)),
                "evidence_coverage": coverage,
                "weights": weights,
            }
        result[variant] = blocks
    return result


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_accuracy_lab_schema(v6_db_path: str | Path) -> None:
    with _connect(v6_db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS v6_shadow_forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                variant TEXT NOT NULL,
                horizon_days INTEGER NOT NULL,
                generated_at TEXT NOT NULL,
                score REAL,
                direction TEXT NOT NULL,
                evidence_coverage REAL NOT NULL,
                profile_json TEXT NOT NULL,
                UNIQUE(signal_id, variant, horizon_days),
                FOREIGN KEY(signal_id) REFERENCES v6_signals(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS v6_shadow_outcomes (
                shadow_forecast_id INTEGER PRIMARY KEY,
                evaluated_at TEXT NOT NULL,
                end_trade_date TEXT NOT NULL,
                start_price REAL NOT NULL,
                end_price REAL NOT NULL,
                return_pct REAL NOT NULL,
                directional_hit INTEGER,
                benchmark_spy_return_pct REAL,
                excess_vs_spy_pct REAL,
                FOREIGN KEY(shadow_forecast_id) REFERENCES v6_shadow_forecasts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS v6_trade_outcomes (
                signal_id INTEGER PRIMARY KEY,
                evaluated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_trade_date TEXT,
                exit_trade_date TEXT,
                entry_price REAL,
                exit_price REAL,
                return_pct REAL,
                r_multiple REAL,
                win INTEGER,
                exit_reason TEXT,
                holding_bars INTEGER,
                mfe_pct REAL,
                mae_pct REAL,
                cost_bps REAL NOT NULL,
                FOREIGN KEY(signal_id) REFERENCES v6_signals(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_v6_shadow_variant_horizon
                ON v6_shadow_forecasts(variant, horizon_days, signal_id);
            CREATE INDEX IF NOT EXISTS ix_v6_shadow_outcomes_eval
                ON v6_shadow_outcomes(evaluated_at);
            CREATE INDEX IF NOT EXISTS ix_v6_trade_outcomes_status
                ON v6_trade_outcomes(status, evaluated_at);
            """
        )


def _alpha_features(raw: str) -> Optional[AlphaFeatures]:
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    allowed = {item.name for item in fields(AlphaFeatures)}
    values = {name: payload.get(name) for name in allowed if name in payload}
    return AlphaFeatures(**values)


def persist_shadow_forecasts(v6_db_path: str | Path) -> int:
    ensure_accuracy_lab_schema(v6_db_path)
    inserted = 0
    with _connect(v6_db_path) as conn:
        signals = conn.execute(
            "SELECT id, instrument_type, features_json FROM v6_signals ORDER BY id"
        ).fetchall()
        for signal in signals:
            features = _alpha_features(str(signal["features_json"] or "{}"))
            if features is None:
                continue
            variants = build_shadow_forecasts(
                features,
                instrument_type=str(signal["instrument_type"] or "STOCK"),
            )
            for variant, blocks in variants.items():
                for block in blocks.values():
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO v6_shadow_forecasts(
                            signal_id, variant, horizon_days, generated_at,
                            score, direction, evidence_coverage, profile_json
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(signal["id"]), variant, int(block["horizon_days"]), _utc_now(),
                            _finite(block.get("score")), str(block.get("direction") or "neutral"),
                            float(block.get("evidence_coverage") or 0.0),
                            json.dumps(block.get("weights") or {}, sort_keys=True, separators=(",", ":")),
                        ),
                    )
                    inserted += max(0, int(cursor.rowcount))
    return inserted


def _stock_columns(stock_db_path: str | Path) -> set[str]:
    with _connect(stock_db_path) as conn:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(stock_daily)").fetchall()}


def _future_bars(stock_db_path: str | Path, *, code: str, analysis_date: str, needed: int) -> list[Dict[str, Any]]:
    columns = _stock_columns(stock_db_path)
    if not {"code", "date", "close"}.issubset(columns):
        return []
    fields = [name for name in ("date", "open", "high", "low", "close") if name in columns]
    with _connect(stock_db_path) as conn:
        rows = conn.execute(
            f"SELECT {','.join(fields)} FROM stock_daily WHERE code=? AND date>? ORDER BY date ASC LIMIT ?",
            (str(code), str(analysis_date), max(1, int(needed))),
        ).fetchall()
    return [dict(row) for row in rows]


def _benchmark_return(stock_db_path: str | Path, *, code: str, analysis_date: str, horizon: int) -> Optional[float]:
    columns = _stock_columns(stock_db_path)
    if not {"code", "date", "close"}.issubset(columns):
        return None
    with _connect(stock_db_path) as conn:
        start = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (str(code), str(analysis_date)),
        ).fetchone()
        future = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date>? ORDER BY date ASC LIMIT ?",
            (str(code), str(analysis_date), int(horizon)),
        ).fetchall()
    if start is None or len(future) < int(horizon):
        return None
    left = _finite(start["close"])
    right = _finite(future[-1]["close"])
    if left is None or right is None or left <= 0:
        return None
    return (right / left - 1.0) * 100.0


def mature_shadow_outcomes(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    neutral_band_pct: float = 2.0,
) -> Dict[str, int]:
    ensure_accuracy_lab_schema(v6_db_path)
    evaluated = 0
    pending = 0
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.horizon_days, f.direction,
                   s.code, s.effective_trade_date, s.analysis_created_at, s.baseline_price
            FROM v6_shadow_forecasts f
            JOIN v6_signals s ON s.id=f.signal_id
            LEFT JOIN v6_shadow_outcomes o ON o.shadow_forecast_id=f.id
            WHERE o.shadow_forecast_id IS NULL
            ORDER BY f.id
            """
        ).fetchall()

        for row in rows:
            horizon = int(row["horizon_days"])
            analysis_date = str(row["effective_trade_date"] or row["analysis_created_at"] or "")[:10]
            start = _finite(row["baseline_price"])
            if not analysis_date or start is None or start <= 0:
                pending += 1
                continue
            bars = _future_bars(
                stock_db_path,
                code=str(row["code"]),
                analysis_date=analysis_date,
                needed=horizon,
            )
            if len(bars) < horizon:
                pending += 1
                continue
            end = _finite(bars[-1].get("close"))
            if end is None or end <= 0:
                pending += 1
                continue
            return_pct = (end / start - 1.0) * 100.0
            direction = str(row["direction"] or "neutral").lower()
            if direction == "bullish":
                hit = int(return_pct > 0.0)
            elif direction == "bearish":
                hit = int(return_pct < 0.0)
            else:
                hit = int(abs(return_pct) <= abs(float(neutral_band_pct)))
            spy_return = _benchmark_return(
                stock_db_path,
                code="SPY",
                analysis_date=analysis_date,
                horizon=horizon,
            )
            excess = None if spy_return is None else return_pct - spy_return
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO v6_shadow_outcomes(
                    shadow_forecast_id, evaluated_at, end_trade_date,
                    start_price, end_price, return_pct, directional_hit,
                    benchmark_spy_return_pct, excess_vs_spy_pct
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(row["id"]), _utc_now(), str(bars[-1].get("date") or ""),
                    start, end, round(return_pct, 6), hit,
                    _round(spy_return, 6), _round(excess, 6),
                ),
            )
            evaluated += max(0, int(cursor.rowcount))
    return {"evaluated": evaluated, "not_yet_mature": pending}


def _parse_plan(raw: str) -> Optional[Dict[str, Any]]:
    try:
        plan = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(plan, dict):
        return None
    zone = plan.get("entry_zone")
    targets = plan.get("targets")
    if not isinstance(zone, (list, tuple)) or len(zone) < 2:
        return None
    if not isinstance(targets, (list, tuple)) or not targets:
        return None
    low = _finite(zone[0])
    high = _finite(zone[1])
    stop = _finite(plan.get("stop_loss"))
    target = _finite(targets[0])
    if None in {low, high, stop, target}:
        return None
    entry_low, entry_high = sorted((float(low), float(high)))
    if entry_low <= 0 or entry_high <= 0 or float(stop) <= 0 or float(target) <= 0:
        return None
    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop": float(stop),
        "target": float(target),
    }


def _bar_value(bar: Mapping[str, Any], key: str, fallback: Optional[float] = None) -> Optional[float]:
    value = _finite(bar.get(key))
    return fallback if value is None else value


def simulate_long_trade(
    bars: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    *,
    cost_bps: float = DEFAULT_COST_BPS,
) -> Dict[str, Any]:
    """Conservative OHLC execution simulation for a BUY_SETUP trade plan.

    Entry requires the future bar range to overlap the planned entry zone. When
    stop and first target are both touched in the same daily bar, stop is assumed
    first to avoid optimistic intrabar ordering. `cost_bps` is a round-trip cost.
    """
    entry_low = _finite(plan.get("entry_low"))
    entry_high = _finite(plan.get("entry_high"))
    stop = _finite(plan.get("stop"))
    target = _finite(plan.get("target"))
    if None in {entry_low, entry_high, stop, target}:
        return {"status": "invalid_plan"}
    entry_low = float(entry_low)
    entry_high = float(entry_high)
    stop = float(stop)
    target = float(target)
    if not (0 < stop < entry_high and target > entry_low):
        return {"status": "invalid_plan"}

    entry_price: Optional[float] = None
    entry_index: Optional[int] = None
    entry_date: Optional[str] = None
    max_high: Optional[float] = None
    min_low: Optional[float] = None
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    exit_index: Optional[int] = None

    for index, raw_bar in enumerate(bars):
        bar = dict(raw_bar)
        close = _bar_value(bar, "close")
        low = _bar_value(bar, "low", close)
        high = _bar_value(bar, "high", close)
        open_price = _bar_value(bar, "open", close)
        if low is None or high is None or close is None:
            continue
        if low > high:
            low, high = high, low

        if entry_price is None:
            if high < entry_low or low > entry_high:
                continue
            if open_price is None:
                fill = min(entry_high, max(entry_low, (low + high) / 2.0))
            elif open_price < entry_low:
                fill = entry_low
            elif open_price > entry_high:
                fill = entry_high
            else:
                fill = open_price
            entry_price = float(fill)
            entry_index = index
            entry_date = str(bar.get("date") or "")

        max_high = high if max_high is None else max(max_high, high)
        min_low = low if min_low is None else min(min_low, low)

        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop and hit_target:
            # Conservative daily-bar ambiguity rule.
            exit_price = min(stop, open_price) if open_price is not None and open_price < stop else stop
            exit_reason = "stop_and_target_same_bar_stop_first"
        elif hit_stop:
            exit_price = min(stop, open_price) if open_price is not None and open_price < stop else stop
            exit_reason = "stop"
        elif hit_target:
            exit_price = target
            exit_reason = "target1"

        if exit_price is not None:
            exit_date = str(bar.get("date") or "")
            exit_index = index
            break

    if entry_price is None or entry_index is None:
        return {"status": "not_filled"}

    if exit_price is None:
        last = dict(bars[-1]) if bars else {}
        last_close = _bar_value(last, "close")
        if last_close is None:
            return {"status": "invalid_bars"}
        exit_price = last_close
        exit_date = str(last.get("date") or "")
        exit_index = len(bars) - 1
        exit_reason = "time_exit"

    gross_return_pct = (float(exit_price) / entry_price - 1.0) * 100.0
    total_cost_pct = max(0.0, float(cost_bps)) / 100.0
    net_return_pct = gross_return_pct - total_cost_pct
    risk_per_share = entry_price - stop
    cost_per_share = entry_price * max(0.0, float(cost_bps)) / 10000.0
    r_multiple = None
    if risk_per_share > 0:
        r_multiple = (float(exit_price) - entry_price - cost_per_share) / risk_per_share
    mfe = None if max_high is None else (max_high / entry_price - 1.0) * 100.0
    mae = None if min_low is None else (min_low / entry_price - 1.0) * 100.0
    return {
        "status": "filled",
        "entry_trade_date": entry_date,
        "exit_trade_date": exit_date,
        "entry_price": round(entry_price, 6),
        "exit_price": round(float(exit_price), 6),
        "return_pct": round(net_return_pct, 6),
        "r_multiple": _round(r_multiple, 6),
        "win": int(net_return_pct > 0.0),
        "exit_reason": exit_reason,
        "holding_bars": max(1, int(exit_index or 0) - int(entry_index) + 1),
        "mfe_pct": _round(mfe, 6),
        "mae_pct": _round(mae, 6),
    }


def mature_trade_outcomes(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
    cost_bps: float = DEFAULT_COST_BPS,
) -> Dict[str, int]:
    ensure_accuracy_lab_schema(v6_db_path)
    evaluated = 0
    pending = 0
    max_holding = max(1, int(max_holding_bars))
    with _connect(v6_db_path) as conn:
        signals = conn.execute(
            """
            SELECT s.id, s.code, s.effective_trade_date, s.analysis_created_at, s.trade_plan_json
            FROM v6_signals s
            LEFT JOIN v6_trade_outcomes t ON t.signal_id=s.id
            WHERE s.decision='BUY_SETUP' AND t.signal_id IS NULL
            ORDER BY s.id
            """
        ).fetchall()
        for signal in signals:
            analysis_date = str(signal["effective_trade_date"] or signal["analysis_created_at"] or "")[:10]
            plan = _parse_plan(str(signal["trade_plan_json"] or "{}"))
            if not analysis_date or plan is None:
                result = {"status": "invalid_plan"}
            else:
                bars = _future_bars(
                    stock_db_path,
                    code=str(signal["code"]),
                    analysis_date=analysis_date,
                    needed=max_holding,
                )
                if len(bars) < max_holding:
                    pending += 1
                    continue
                result = simulate_long_trade(bars, plan, cost_bps=cost_bps)

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO v6_trade_outcomes(
                    signal_id, evaluated_at, status, entry_trade_date, exit_trade_date,
                    entry_price, exit_price, return_pct, r_multiple, win, exit_reason,
                    holding_bars, mfe_pct, mae_pct, cost_bps
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(signal["id"]), _utc_now(), str(result.get("status") or "unknown"),
                    result.get("entry_trade_date"), result.get("exit_trade_date"),
                    _finite(result.get("entry_price")), _finite(result.get("exit_price")),
                    _finite(result.get("return_pct")), _finite(result.get("r_multiple")),
                    result.get("win"), result.get("exit_reason"), result.get("holding_bars"),
                    _finite(result.get("mfe_pct")), _finite(result.get("mae_pct")),
                    float(cost_bps),
                ),
            )
            evaluated += max(0, int(cursor.rowcount))
    return {"evaluated": evaluated, "not_yet_mature": pending}


def _date_indices(stock_db_path: str | Path, codes: Sequence[str]) -> Dict[str, Dict[str, int]]:
    wanted = tuple(dict.fromkeys(str(code).upper() for code in codes if str(code).strip()))
    if not wanted:
        return {}
    columns = _stock_columns(stock_db_path)
    if not {"code", "date"}.issubset(columns):
        return {}
    placeholders = ",".join("?" for _ in wanted)
    with _connect(stock_db_path) as conn:
        rows = conn.execute(
            f"SELECT code,date FROM stock_daily WHERE code IN ({placeholders}) ORDER BY code,date",
            wanted,
        ).fetchall()
    result: Dict[str, Dict[str, int]] = {}
    counters: Dict[str, int] = {}
    for row in rows:
        code = str(row["code"] or "").upper()
        date_text = str(row["date"] or "")
        index = counters.get(code, 0)
        result.setdefault(code, {})[date_text] = index
        counters[code] = index + 1
    return result


def _non_overlapping(rows: Sequence[Mapping[str, Any]], *, horizon: int, date_indices: Mapping[str, Mapping[str, int]]) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    last_index: Dict[str, int] = {}
    for row in sorted(rows, key=lambda item: (str(item.get("code") or ""), str(item.get("effective_trade_date") or ""), int(item.get("signal_id") or 0))):
        code = str(row.get("code") or "").upper()
        date_text = str(row.get("effective_trade_date") or "")[:10]
        current = (date_indices.get(code) or {}).get(date_text)
        if current is None:
            continue
        previous = last_index.get(code)
        if previous is None or current - previous >= int(horizon):
            selected.append(row)
            last_index[code] = current
    return selected


def _hit_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    hits = [int(row.get("directional_hit")) for row in rows if row.get("directional_hit") is not None]
    successes = sum(hits)
    total = len(hits)
    lower, upper = wilson_interval(successes, total)
    returns = [_finite(row.get("return_pct")) for row in rows]
    clean_returns = [value for value in returns if value is not None]
    excess = [_finite(row.get("excess_vs_spy_pct")) for row in rows]
    clean_excess = [value for value in excess if value is not None]
    return {
        "samples": len(rows),
        "directional_samples": total,
        "directional_hits": successes,
        "directional_hit_rate_pct": None if total == 0 else round(100.0 * successes / total, 2),
        "hit_rate_ci95_low_pct": lower,
        "hit_rate_ci95_high_pct": upper,
        "avg_return_pct": None if not clean_returns else round(statistics.fmean(clean_returns), 4),
        "avg_excess_vs_spy_pct": None if not clean_excess else round(statistics.fmean(clean_excess), 4),
    }


def _research_state(metrics: Mapping[str, Any], minimum: int) -> str:
    samples = int(metrics.get("directional_samples") or 0)
    lower = _finite(metrics.get("hit_rate_ci95_low_pct"))
    if samples < max(3, int(minimum)):
        return "insufficient_data"
    if lower is not None and lower > 50.0:
        return "evidence_above_chance"
    return "measurable_unproven"


def _champion_rows(v6_db_path: str | Path) -> list[Dict[str, Any]]:
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.id AS signal_id, s.code, s.effective_trade_date, s.instrument_type,
                   s.market_regime, o.horizon_days, o.return_pct, o.directional_hit,
                   o.excess_vs_spy_pct
            FROM v6_outcomes o
            JOIN v6_signals s ON s.id=o.signal_id
            ORDER BY o.horizon_days, s.code, s.effective_trade_date, s.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _shadow_rows(v6_db_path: str | Path) -> list[Dict[str, Any]]:
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.id AS signal_id, s.code, s.effective_trade_date, s.instrument_type,
                   s.market_regime, f.variant, f.horizon_days, o.return_pct,
                   o.directional_hit, o.excess_vs_spy_pct
            FROM v6_shadow_outcomes o
            JOIN v6_shadow_forecasts f ON f.id=o.shadow_forecast_id
            JOIN v6_signals s ON s.id=f.signal_id
            ORDER BY f.variant, f.horizon_days, s.code, s.effective_trade_date, s.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _group_breakdown(rows: Sequence[Mapping[str, Any]], field: str) -> list[Dict[str, Any]]:
    result: list[Dict[str, Any]] = []
    values = sorted({str(row.get(field) or "unknown") for row in rows})
    for value in values:
        group = [row for row in rows if str(row.get(field) or "unknown") == value]
        metrics = _hit_metrics(group)
        result.append({"name": value, **metrics})
    return result


def _champion_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    stock_db_path: str | Path,
    min_samples: int,
) -> list[Dict[str, Any]]:
    codes = [str(row.get("code") or "") for row in rows]
    indices = _date_indices(stock_db_path, codes)
    result: list[Dict[str, Any]] = []
    for horizon in sorted({int(row.get("horizon_days") or 0) for row in rows if int(row.get("horizon_days") or 0) > 0}):
        bucket = [row for row in rows if int(row.get("horizon_days") or 0) == horizon]
        raw = _hit_metrics(bucket)
        independent_rows = _non_overlapping(bucket, horizon=horizon, date_indices=indices)
        independent = _hit_metrics(independent_rows)
        result.append(
            {
                "horizon_days": horizon,
                "raw": raw,
                "non_overlapping": independent,
                "research_state": _research_state(independent, min_samples),
                "by_instrument": _group_breakdown(bucket, "instrument_type"),
                "by_market_regime": _group_breakdown(bucket, "market_regime"),
            }
        )
    return result


def _shadow_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    stock_db_path: str | Path,
    champion: Sequence[Mapping[str, Any]],
    promotion_min_samples: int,
) -> list[Dict[str, Any]]:
    codes = [str(row.get("code") or "") for row in rows]
    indices = _date_indices(stock_db_path, codes)
    champion_by_horizon = {int(item.get("horizon_days") or 0): item for item in champion}
    result: list[Dict[str, Any]] = []
    variants = sorted({str(row.get("variant") or "") for row in rows if str(row.get("variant") or "")})
    for variant in variants:
        for horizon in sorted({int(row.get("horizon_days") or 0) for row in rows if row.get("variant") == variant}):
            bucket = [row for row in rows if str(row.get("variant") or "") == variant and int(row.get("horizon_days") or 0) == horizon]
            raw = _hit_metrics(bucket)
            independent_rows = _non_overlapping(bucket, horizon=horizon, date_indices=indices)
            independent = _hit_metrics(independent_rows)
            champion_item = champion_by_horizon.get(horizon) or {}
            champion_ind = champion_item.get("non_overlapping") or {}
            challenger_hit = _finite(independent.get("directional_hit_rate_pct"))
            champion_hit = _finite(champion_ind.get("directional_hit_rate_pct"))
            challenger_low = _finite(independent.get("hit_rate_ci95_low_pct"))
            champion_low = _finite(champion_ind.get("hit_rate_ci95_low_pct"))
            challenger_excess = _finite(independent.get("avg_excess_vs_spy_pct"))
            champion_excess = _finite(champion_ind.get("avg_excess_vs_spy_pct"))
            enough = (
                int(independent.get("directional_samples") or 0) >= int(promotion_min_samples)
                and int(champion_ind.get("directional_samples") or 0) >= int(promotion_min_samples)
            )
            hit_delta = None if challenger_hit is None or champion_hit is None else challenger_hit - champion_hit
            excess_ok = (
                challenger_excess is None
                or champion_excess is None
                or challenger_excess >= champion_excess
            )
            promotion_candidate = bool(
                enough
                and challenger_hit is not None
                and champion_hit is not None
                and challenger_low is not None
                and champion_low is not None
                and challenger_low > 50.0
                and challenger_low >= champion_low
                and challenger_hit >= champion_hit + 2.0
                and excess_ok
            )
            result.append(
                {
                    "variant": variant,
                    "horizon_days": horizon,
                    "raw": raw,
                    "non_overlapping": independent,
                    "hit_rate_delta_vs_champion_pp": _round(hit_delta, 2),
                    "promotion_candidate": promotion_candidate,
                }
            )
    return result


def _max_drawdown(returns_pct: Sequence[float]) -> Optional[float]:
    if not returns_pct:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns_pct:
        equity *= 1.0 + float(value) / 100.0
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1.0)
    return round(100.0 * max_dd, 4)


def _strategy_metrics(v6_db_path: str | Path, min_samples: int) -> Dict[str, Any]:
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            """
            SELECT t.*, s.code, s.instrument_type, s.market_regime
            FROM v6_trade_outcomes t
            JOIN v6_signals s ON s.id=t.signal_id
            ORDER BY COALESCE(t.entry_trade_date, t.evaluated_at), t.signal_id
            """
        ).fetchall()
    items = [dict(row) for row in rows]
    filled = [item for item in items if str(item.get("status") or "") == "filled" and _finite(item.get("return_pct")) is not None]
    returns = [float(item["return_pct"]) for item in filled]
    wins = sum(int(item.get("win") or 0) for item in filled)
    lower, upper = wilson_interval(wins, len(filled))
    positive = sum(value for value in returns if value > 0)
    negative = abs(sum(value for value in returns if value < 0))
    profit_factor = None if negative <= 0 else positive / negative
    r_values = [value for value in (_finite(item.get("r_multiple")) for item in filled) if value is not None]
    target_hits = sum(1 for item in filled if str(item.get("exit_reason") or "") == "target1")
    stop_hits = sum(1 for item in filled if "stop" in str(item.get("exit_reason") or ""))
    return {
        "status": "measurable" if len(filled) >= max(3, int(min_samples)) else "insufficient_data",
        "evaluated_plans": len(items),
        "filled_trades": len(filled),
        "unfilled_or_invalid": len(items) - len(filled),
        "win_rate_pct": None if not filled else round(100.0 * wins / len(filled), 2),
        "win_rate_ci95_low_pct": lower,
        "win_rate_ci95_high_pct": upper,
        "avg_return_pct": None if not returns else round(statistics.fmean(returns), 4),
        "median_return_pct": None if not returns else round(statistics.median(returns), 4),
        "avg_r_multiple": None if not r_values else round(statistics.fmean(r_values), 4),
        "profit_factor": None if profit_factor is None else round(profit_factor, 4),
        "max_drawdown_pct": _max_drawdown(returns),
        "target1_hit_rate_pct": None if not filled else round(100.0 * target_hits / len(filled), 2),
        "stop_hit_rate_pct": None if not filled else round(100.0 * stop_hits / len(filled), 2),
        "by_instrument": _group_breakdown(filled, "instrument_type"),
        "by_market_regime": _group_breakdown(filled, "market_regime"),
        "execution_policy": "BUY_SETUP only; first target; conservative stop-first on same-bar ambiguity",
    }


def build_accuracy_lab_report(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    min_samples: int = 50,
    promotion_min_samples: int = DEFAULT_PROMOTION_MIN_SAMPLES,
    cost_bps: float = DEFAULT_COST_BPS,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
) -> Dict[str, Any]:
    champion_rows = _champion_rows(v6_db_path)
    champion = _champion_metrics(
        champion_rows,
        stock_db_path=stock_db_path,
        min_samples=min_samples,
    )
    shadow = _shadow_metrics(
        _shadow_rows(v6_db_path),
        stock_db_path=stock_db_path,
        champion=champion,
        promotion_min_samples=max(int(promotion_min_samples), int(min_samples)),
    )
    candidates = [
        {"variant": item["variant"], "horizon_days": item["horizon_days"]}
        for item in shadow
        if item.get("promotion_candidate")
    ]
    return {
        "version": ACCURACY_LAB_VERSION,
        "generated_at": _utc_now(),
        "status": "measurable" if any(item.get("research_state") != "insufficient_data" for item in champion) else "insufficient_data",
        "minimum_samples": max(3, int(min_samples)),
        "promotion_min_samples": max(int(promotion_min_samples), int(min_samples)),
        "champion": champion,
        "challengers": shadow,
        "promotion_candidates": candidates,
        "strategy": _strategy_metrics(v6_db_path, min_samples),
        "policy": {
            "auto_promotion": False,
            "auto_weight_tuning": False,
            "non_overlapping_validation": True,
            "confidence_interval": "Wilson 95%",
            "promotion_rule": "research-only; requires >=2pp non-overlap hit-rate lift, non-inferior CI lower bound, lower bound >50%, and no worse SPY excess when available",
            "transaction_cost_bps_round_trip": float(cost_bps),
            "trade_max_holding_bars": max(1, int(max_holding_bars)),
        },
    }


def render_accuracy_lab_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# V6.2 Accuracy Lab",
        "",
        "> 该报告只评估预测与交易计划，不会自动调权、自动升级 Challenger 或自动下单。",
        "",
        f"- 状态：**{payload.get('status', 'insufficient_data')}**",
        f"- 成熟样本展示门槛：**{payload.get('minimum_samples', 50)}**",
        f"- Challenger 晋级研究门槛：**{payload.get('promotion_min_samples', 100)}**",
        "",
        "## Champion：方向准确率",
        "",
        "| 周期 | 原始N | 原始命中 | 非重叠N | 非重叠命中 | 95% CI | 状态 |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    champion = list(payload.get("champion") or [])
    for item in champion:
        raw = item.get("raw") or {}
        independent = item.get("non_overlapping") or {}
        low = independent.get("hit_rate_ci95_low_pct")
        high = independent.get("hit_rate_ci95_high_pct")
        ci = "N/A" if low is None or high is None else f"{low:.1f}%–{high:.1f}%"
        lines.append(
            "| {h}D | {rn} | {rh} | {n} | {hit} | {ci} | {state} |".format(
                h=item.get("horizon_days"),
                rn=raw.get("directional_samples", 0),
                rh="N/A" if raw.get("directional_hit_rate_pct") is None else f"{raw.get('directional_hit_rate_pct'):.1f}%",
                n=independent.get("directional_samples", 0),
                hit="N/A" if independent.get("directional_hit_rate_pct") is None else f"{independent.get('directional_hit_rate_pct'):.1f}%",
                ci=ci,
                state=item.get("research_state"),
            )
        )
    if not champion:
        lines.append("| - | 0 | N/A | 0 | N/A | N/A | insufficient_data |")

    lines.extend([
        "",
        "## Challenger Shadow",
        "",
        "| 变体 | 周期 | 非重叠N | 命中 | 相对Champion | 候选晋级 |",
        "|---|---:|---:|---:|---:|---|",
    ])
    challengers = list(payload.get("challengers") or [])
    for item in challengers:
        independent = item.get("non_overlapping") or {}
        hit = independent.get("directional_hit_rate_pct")
        delta = item.get("hit_rate_delta_vs_champion_pp")
        lines.append(
            "| {variant} | {h}D | {n} | {hit} | {delta} | {candidate} |".format(
                variant=item.get("variant"),
                h=item.get("horizon_days"),
                n=independent.get("directional_samples", 0),
                hit="N/A" if hit is None else f"{float(hit):.1f}%",
                delta="N/A" if delta is None else f"{float(delta):+.1f}pp",
                candidate="是（仅研究）" if item.get("promotion_candidate") else "否",
            )
        )
    if not challengers:
        lines.append("| - | - | 0 | N/A | N/A | 否 |")

    strategy = payload.get("strategy") or {}
    lines.extend([
        "",
        "## BUY_SETUP 执行回测",
        "",
        f"- 已成交样本：**{strategy.get('filled_trades', 0)}**",
        f"- 胜率：**{strategy.get('win_rate_pct') if strategy.get('win_rate_pct') is not None else 'N/A'}**",
        f"- 平均收益：**{strategy.get('avg_return_pct') if strategy.get('avg_return_pct') is not None else 'N/A'}%**",
        f"- 平均 R：**{strategy.get('avg_r_multiple') if strategy.get('avg_r_multiple') is not None else 'N/A'}**",
        f"- Profit Factor：**{strategy.get('profit_factor') if strategy.get('profit_factor') is not None else 'N/A'}**",
        f"- 最大回撤：**{strategy.get('max_drawdown_pct') if strategy.get('max_drawdown_pct') is not None else 'N/A'}%**",
        "",
        "## 方法与安全门",
        "",
        "- 方向命中率同时展示全部成熟样本与按交易周期去重后的非重叠样本。",
        "- 95% 置信区间使用 Wilson 区间；样本不足时不把点估计当作稳定胜率。",
        "- Challenger 与 Champion 同步影子预测，绝不直接改变正式日报的预测权重。",
        "- BUY_SETUP 执行回测使用未来 OHLC，若同一日同时触发止损和目标，按止损先发生处理。",
        "- 当前只允许输出研究候选；自动调权和自动晋级明确关闭。",
        "",
        f"*Generated by {payload.get('version', ACCURACY_LAB_VERSION)} at {payload.get('generated_at', '-')}*",
    ])
    return "\n".join(lines) + "\n"


def write_accuracy_lab_report(payload: Mapping[str, Any], report_dir: str | Path) -> Dict[str, str]:
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "v6_accuracy_lab.json"
    md_path = output / "v6_accuracy_lab.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_accuracy_lab_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def run_accuracy_lab(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    report_dir: str | Path,
    min_samples: int = 50,
    promotion_min_samples: int = DEFAULT_PROMOTION_MIN_SAMPLES,
    cost_bps: float = DEFAULT_COST_BPS,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
) -> Dict[str, Any]:
    ensure_accuracy_lab_schema(v6_db_path)
    new_shadow = persist_shadow_forecasts(v6_db_path)
    shadow_maturation = mature_shadow_outcomes(v6_db_path, stock_db_path)
    trade_maturation = mature_trade_outcomes(
        v6_db_path,
        stock_db_path,
        max_holding_bars=max_holding_bars,
        cost_bps=cost_bps,
    )
    payload = build_accuracy_lab_report(
        v6_db_path,
        stock_db_path,
        min_samples=min_samples,
        promotion_min_samples=promotion_min_samples,
        cost_bps=cost_bps,
        max_holding_bars=max_holding_bars,
    )
    payload["run"] = {
        "new_shadow_forecasts": new_shadow,
        "new_shadow_outcomes": shadow_maturation["evaluated"],
        "shadow_not_yet_mature": shadow_maturation["not_yet_mature"],
        "new_trade_outcomes": trade_maturation["evaluated"],
        "trade_not_yet_mature": trade_maturation["not_yet_mature"],
    }
    payload["artifacts"] = write_accuracy_lab_report(payload, report_dir)
    return payload
