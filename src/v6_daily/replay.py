from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.alpha_engine.models import AlphaFeatures

from .accuracy import build_horizon_forecasts, classify_instrument


@dataclass(frozen=True)
class ReplayObservation:
    code: str
    as_of: str
    horizon_days: int
    score: Optional[float]
    direction: str
    future_return_pct: float
    directional_hit: Optional[int]
    excess_vs_spy_pct: Optional[float]


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _ret(closes: Sequence[float], days: int) -> Optional[float]:
    if len(closes) <= days or closes[-days - 1] <= 0:
        return None
    return (closes[-1] / closes[-days - 1] - 1.0) * 100.0


def _return_score(value: Optional[float], scale: float) -> Optional[float]:
    if value is None:
        return None
    return round(_clamp(50.0 + 50.0 * math.tanh(value / scale)), 2)


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [value for value in values if value is not None]
    return None if not clean else round(sum(clean) / len(clean), 2)


def _stdev_returns(closes: Sequence[float], window: int = 20) -> Optional[float]:
    if len(closes) < window + 1:
        return None
    returns = []
    sample = closes[-window - 1 :]
    for left, right in zip(sample, sample[1:]):
        if left > 0:
            returns.append((right / left - 1.0) * 100.0)
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252.0)


def _ma(values: Sequence[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def _benchmark_return(rows_by_date: Mapping[str, Mapping[str, Any]], dates: Sequence[str], as_of_index: int, days: int) -> Optional[float]:
    if as_of_index < days or as_of_index >= len(dates):
        return None
    current = rows_by_date.get(dates[as_of_index])
    previous = rows_by_date.get(dates[as_of_index - days])
    now = _finite(current.get("close")) if isinstance(current, dict) else None
    before = _finite(previous.get("close")) if isinstance(previous, dict) else None
    if now is None or before is None or before <= 0:
        return None
    return (now / before - 1.0) * 100.0


def _features_at(
    code: str,
    rows: Sequence[Mapping[str, Any]],
    index: int,
    benchmarks: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> AlphaFeatures:
    history = rows[: index + 1]
    closes = [float(row["close"]) for row in history if _finite(row.get("close")) is not None]
    ret5, ret20, ret60 = _ret(closes, 5), _ret(closes, 20), _ret(closes, 60)
    ma20, ma50 = _ma(closes, 20), _ma(closes, 50)
    price = closes[-1]

    trend_parts: list[float] = []
    if ma20 is not None:
        trend_parts.append(70.0 if price >= ma20 else 30.0)
    if ma20 is not None and ma50 is not None:
        trend_parts.append(75.0 if ma20 >= ma50 else 25.0)
    trend_parts.extend(value for value in (_return_score(ret20, 8.0), _return_score(ret60, 16.0)) if value is not None)
    trend = None if not trend_parts else round(statistics.fmean(trend_parts), 2)
    momentum = _mean((_return_score(ret5, 4.0), _return_score(ret20, 8.0)))

    current_date = str(history[-1].get("date") or "")
    relative_parts: list[Optional[float]] = []
    for benchmark in ("SPY", "QQQ"):
        mapping = benchmarks.get(benchmark) or {}
        dates = sorted(mapping)
        if current_date not in mapping:
            continue
        position = dates.index(current_date)
        for horizon, scale in ((20, 6.0), (60, 12.0)):
            market_ret = _benchmark_return(mapping, dates, position, horizon)
            stock_ret = ret20 if horizon == 20 else ret60
            if market_ret is not None and stock_ret is not None:
                relative_parts.append(_return_score(stock_ret - market_ret, scale))
    relative_strength = _mean(relative_parts)

    volumes = [_finite(row.get("volume")) for row in history[-21:]]
    clean_volumes = [value for value in volumes if value is not None and value >= 0]
    volume_confirmation = None
    if len(clean_volumes) >= 6 and clean_volumes[-1] is not None:
        denominator = statistics.fmean(clean_volumes[:-1]) if clean_volumes[:-1] else 0.0
        if denominator > 0:
            rvol = clean_volumes[-1] / denominator
            intensity = math.tanh((rvol - 1.0) / 0.6)
            direction = max(-1.0, min(1.0, (ret5 or 0.0) / 3.0))
            volume_confirmation = round(_clamp(50.0 + 35.0 * intensity * direction), 2)

    spy_mapping = benchmarks.get("SPY") or {}
    market_regime = None
    if current_date in spy_mapping:
        dates = sorted(spy_mapping)
        position = dates.index(current_date)
        spy20 = _benchmark_return(spy_mapping, dates, position, 20)
        spy60 = _benchmark_return(spy_mapping, dates, position, 60)
        market_regime = _mean((_return_score(spy20, 6.0), _return_score(spy60, 12.0)))

    realized = _stdev_returns(closes, 20)
    volatility_risk = None if realized is None else round(_clamp(100.0 * realized / (realized + 28.0)), 2)
    observed = [trend, momentum, relative_strength, volume_confirmation, market_regime, volatility_risk]
    data_quality = round(100.0 * sum(value is not None for value in observed) / len(observed), 2)
    return AlphaFeatures(
        trend=trend,
        momentum=momentum,
        relative_strength=relative_strength,
        volume_confirmation=volume_confirmation,
        market_regime=market_regime,
        volatility_risk=volatility_risk,
        data_quality=data_quality,
    )


def load_sqlite_series(stock_db_path: str, codes: Optional[Sequence[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{Path(stock_db_path)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(stock_daily)").fetchall()}
        fields = [name for name in ("date", "open", "high", "low", "close", "volume") if name in columns]
        if "date" not in fields or "close" not in fields:
            return {}
        sql = f"SELECT code,{','.join(fields)} FROM stock_daily"
        params: Tuple[Any, ...] = ()
        if codes:
            normalized = tuple(dict.fromkeys(str(code).upper() for code in codes))
            placeholders = ",".join("?" for _ in normalized)
            sql += f" WHERE code IN ({placeholders})"
            params = normalized
        sql += " ORDER BY code,date"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        code = str(item.pop("code") or "").upper()
        if code:
            result.setdefault(code, []).append(item)
    return result


def replay_series(
    series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    codes: Optional[Sequence[str]] = None,
    min_lookback: int = 60,
    horizons: Sequence[int] = (5, 10, 20),
) -> List[ReplayObservation]:
    selected = [str(code).upper() for code in (codes or series.keys()) if str(code).upper() not in {"SPY", "QQQ"}]
    benchmarks = {
        code: {str(row.get("date")): dict(row) for row in series.get(code, ()) if row.get("date")}
        for code in ("SPY", "QQQ")
    }
    observations: List[ReplayObservation] = []
    max_horizon = max(int(h) for h in horizons)
    for code in selected:
        rows = list(series.get(code) or ())
        if len(rows) <= min_lookback + max_horizon:
            continue
        instrument = classify_instrument(code, {})
        for index in range(min_lookback, len(rows) - max_horizon):
            close = _finite(rows[index].get("close"))
            if close is None or close <= 0:
                continue
            features = _features_at(code, rows, index, benchmarks)
            forecasts = build_horizon_forecasts(features, instrument_type=instrument)
            for horizon in horizons:
                future = _finite(rows[index + int(horizon)].get("close"))
                if future is None or future <= 0:
                    continue
                future_return = (future / close - 1.0) * 100.0
                block = forecasts.get(f"{int(horizon)}d") or {}
                direction = str(block.get("direction") or "neutral")
                if direction == "bullish":
                    hit = int(future_return > 0)
                elif direction == "bearish":
                    hit = int(future_return < 0)
                else:
                    hit = int(abs(future_return) <= 2.0)

                spy_excess = None
                as_of = str(rows[index].get("date") or "")
                spy = benchmarks.get("SPY") or {}
                if as_of in spy:
                    dates = sorted(spy)
                    pos = dates.index(as_of)
                    if pos + int(horizon) < len(dates):
                        start = _finite(spy[dates[pos]].get("close"))
                        end = _finite(spy[dates[pos + int(horizon)]].get("close"))
                        if start is not None and end is not None and start > 0:
                            spy_return = (end / start - 1.0) * 100.0
                            spy_excess = future_return - spy_return
                observations.append(
                    ReplayObservation(
                        code=code,
                        as_of=as_of,
                        horizon_days=int(horizon),
                        score=_finite(block.get("score")),
                        direction=direction,
                        future_return_pct=round(future_return, 6),
                        directional_hit=hit,
                        excess_vs_spy_pct=None if spy_excess is None else round(spy_excess, 6),
                    )
                )
    return observations


def summarize_replay(observations: Sequence[ReplayObservation]) -> Dict[str, Any]:
    horizons = []
    for horizon in sorted({item.horizon_days for item in observations}):
        rows = [item for item in observations if item.horizon_days == horizon]
        hits = [item.directional_hit for item in rows if item.directional_hit is not None]
        returns = [item.future_return_pct for item in rows]
        excess = [item.excess_vs_spy_pct for item in rows if item.excess_vs_spy_pct is not None]
        yearly = []
        for year in sorted({item.as_of[:4] for item in rows if len(item.as_of) >= 4}):
            group = [item for item in rows if item.as_of.startswith(year)]
            group_hits = [item.directional_hit for item in group if item.directional_hit is not None]
            yearly.append({
                "year": year,
                "samples": len(group),
                "directional_hit_rate_pct": None if not group_hits else round(100.0 * sum(group_hits) / len(group_hits), 2),
            })
        horizons.append({
            "horizon_days": horizon,
            "samples": len(rows),
            "directional_hit_rate_pct": None if not hits else round(100.0 * sum(hits) / len(hits), 2),
            "avg_return_pct": None if not returns else round(statistics.fmean(returns), 4),
            "avg_excess_vs_spy_pct": None if not excess else round(statistics.fmean(excess), 4),
            "yearly_walk_forward": yearly,
        })
    return {"observations": len(observations), "horizons": horizons, "method": "strict no-lookahead rolling replay"}


def replay_stock_db(stock_db_path: str, *, codes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    requested = list(codes or [])
    source_codes = list(dict.fromkeys(requested + ["SPY", "QQQ"])) if requested else None
    series = load_sqlite_series(stock_db_path, source_codes)
    observations = replay_series(series, codes=requested or None)
    return summarize_replay(observations)
