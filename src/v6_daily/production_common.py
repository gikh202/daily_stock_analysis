from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


V6_PRODUCTION_SCOREBOARD_SCHEMA_VERSION = "v6.1-accuracy.1"
DEFAULT_HORIZONS = (5, 10, 20)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def horizon_days(value: Any) -> Optional[int]:
    match = re.search(r"(\d+)", str(value or ""))
    if not match:
        return None
    try:
        days = int(match.group(1))
    except ValueError:
        return None
    return days if days > 0 else None


def average_ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[indexed[position][0]] = rank
        cursor = end
    return ranks


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / denom


def spearman(pairs: Iterable[Tuple[Any, Any]]) -> Tuple[Optional[float], int]:
    clean: List[Tuple[float, float]] = []
    for left, right in pairs:
        x = finite(left)
        y = finite(right)
        if x is not None and y is not None:
            clean.append((x, y))
    if len(clean) < 3:
        return None, len(clean)
    xs = [x for x, _ in clean]
    ys = [y for _, y in clean]
    return pearson(average_ranks(xs), average_ranks(ys)), len(clean)


def return_hit_rate(rows: Sequence[sqlite3.Row], *, positive: bool) -> Optional[float]:
    returns = [value for value in (finite(row["return_pct"]) for row in rows) if value is not None]
    if not returns:
        return None
    hits = sum(1 for value in returns if (value > 0.0 if positive else value <= 0.0))
    return round(100.0 * hits / len(returns), 2)


def calibration(bucket: Sequence[sqlite3.Row], minimum: int) -> Dict[str, Any]:
    ranges = ((0, 40), (40, 50), (50, 60), (60, 70), (70, 101))
    result = []
    for low, high in ranges:
        rows = [
            row
            for row in bucket
            if finite(row["outcome_forecast_score"]) is not None
            and low <= float(row["outcome_forecast_score"]) < high
        ]
        returns = [float(row["return_pct"]) for row in rows if row["return_pct"] is not None]
        if not rows:
            continue
        result.append(
            {
                "score_range": f"{low}-{high if high <= 100 else 100}",
                "samples": len(rows),
                "mature": len(rows) >= minimum,
                "positive_return_rate_pct": None
                if not returns
                else round(100.0 * sum(1 for value in returns if value > 0) / len(returns), 2),
                "avg_return_pct": None if not returns else round(statistics.fmean(returns), 4),
            }
        )
    return {
        "status": "measurable" if any(item["mature"] for item in result) else "insufficient_data",
        "buckets": result,
    }
