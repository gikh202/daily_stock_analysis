from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.accuracy_lab import wilson_interval
from src.v6_daily.lab_replay import AccuracyReplayObservation, replay_accuracy_lab
from src.v6_daily.replay import load_sqlite_series

HORIZONS = (5, 10, 20)
VARIANTS = ("baseline_5d", "confirm_10d", "confirm_10d_veto_20d")


def _direction(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in {"bullish", "bearish", "neutral"} else "neutral"


def _aligned(observations: Sequence[AccuracyReplayObservation]) -> list[dict[int, AccuracyReplayObservation]]:
    grouped: dict[tuple[str, str, int], dict[int, AccuracyReplayObservation]] = {}
    for item in observations:
        if item.variant != "champion" or int(item.horizon_days) not in HORIZONS:
            continue
        key = (item.code, item.as_of, int(item.as_of_index))
        grouped.setdefault(key, {})[int(item.horizon_days)] = item
    result = [block for block in grouped.values() if all(h in block for h in HORIZONS)]
    return sorted(result, key=lambda block: (block[5].code, block[5].as_of_index))


def _qualifies(block: Mapping[int, AccuracyReplayObservation], variant: str) -> bool:
    five, ten, twenty = block[5], block[10], block[20]
    d5, d10, d20 = map(_direction, (five.direction, ten.direction, twenty.direction))
    if d5 not in {"bullish", "bearish"} or five.alpha_target_hit is None:
        return False
    if variant == "baseline_5d":
        return True
    if d10 != d5:
        return False
    if variant == "confirm_10d":
        return True
    opposite = "bearish" if d5 == "bullish" else "bullish"
    return d20 != opposite


def _non_overlap(rows: Sequence[dict[int, AccuracyReplayObservation]]) -> list[dict[int, AccuracyReplayObservation]]:
    selected: list[dict[int, AccuracyReplayObservation]] = []
    last: dict[str, int] = {}
    for block in rows:
        item = block[5]
        previous = last.get(item.code)
        if previous is None or item.as_of_index - previous >= 5:
            selected.append(block)
            last[item.code] = item.as_of_index
    return selected


def _excursions(
    series: Mapping[str, Sequence[Mapping[str, Any]]],
    rows: Sequence[dict[int, AccuracyReplayObservation]],
) -> dict[tuple[str, str, int], tuple[float, float]]:
    result: dict[tuple[str, str, int], tuple[float, float]] = {}
    for block in rows:
        item = block[5]
        history = list(series.get(item.code) or ())
        if item.as_of_index < 0 or item.as_of_index >= len(history):
            continue
        try:
            current = float(history[item.as_of_index]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        future = history[item.as_of_index + 1 : item.as_of_index + 6]
        highs, lows = [], []
        for row in future:
            try:
                highs.append(float(row.get("high", row["close"])))
                lows.append(float(row.get("low", row["close"])))
            except (KeyError, TypeError, ValueError):
                pass
        if current <= 0 or not highs or not lows:
            continue
        if _direction(item.direction) == "bullish":
            mfe = (max(highs) / current - 1.0) * 100.0
            mae = (min(lows) / current - 1.0) * 100.0
        else:
            mfe = (1.0 - min(lows) / current) * 100.0
            mae = (1.0 - max(highs) / current) * 100.0
        result[(item.code, item.as_of, item.as_of_index)] = (mfe, mae)
    return result


def _event_drawdown(rows: Sequence[dict[int, AccuracyReplayObservation]], attr: str) -> float | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for block in rows:
        item = block[5]
        value = getattr(item, attr, None)
        if value is not None and math.isfinite(float(value)):
            grouped[item.as_of].append(float(value))
    if not grouped:
        return None
    equity = peak = 1.0
    drawdown = 0.0
    for day in sorted(grouped):
        equity *= max(0.0, 1.0 + statistics.fmean(grouped[day]) / 100.0)
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1.0)
    return round(100.0 * drawdown, 4)


def _metric(
    rows: Sequence[dict[int, AccuracyReplayObservation]],
    excursions: Mapping[tuple[str, str, int], tuple[float, float]],
) -> dict[str, Any]:
    five = [block[5] for block in rows]
    n = len(five)
    hits = sum(int(item.alpha_target_hit or 0) for item in five)
    low, high = wilson_interval(hits, n)
    alpha = [float(item.alpha_trade_return_pct) for item in five if item.alpha_trade_return_pct is not None]
    strategy = [float(item.strategy_return_pct) for item in five]
    paths = [excursions[(i.code, i.as_of, i.as_of_index)] for i in five if (i.code, i.as_of, i.as_of_index) in excursions]
    mean = lambda values: None if not values else round(statistics.fmean(values), 4)
    median = lambda values: None if not values else round(statistics.median(values), 4)
    return {
        "samples": n,
        "alpha_hit_rate_pct": None if not n else round(100.0 * hits / n, 2),
        "alpha_hit_ci95_low_pct": low,
        "alpha_hit_ci95_high_pct": high,
        "avg_alpha_trade_return_pct": mean(alpha),
        "median_alpha_trade_return_pct": median(alpha),
        "avg_directional_strategy_return_pct": mean(strategy),
        "avg_mfe_5d_pct": mean([x[0] for x in paths]),
        "avg_mae_5d_pct": mean([x[1] for x in paths]),
        "max_drawdown_directional_strategy_pct": _event_drawdown(rows, "strategy_return_pct"),
        "max_drawdown_alpha_pct": _event_drawdown(rows, "alpha_trade_return_pct"),
    }


def evaluate(
    observations: Sequence[AccuracyReplayObservation],
    series: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    aligned = _aligned(observations)
    paths = _excursions(series, aligned)
    baseline = [block for block in aligned if _qualifies(block, "baseline_5d")]
    results = []
    for variant in VARIANTS:
        raw = [block for block in aligned if _qualifies(block, variant)]
        independent = _non_overlap(raw)
        yearly = []
        for year in sorted({block[5].as_of[:4] for block in independent}):
            yearly.append({"year": year, **_metric([b for b in independent if b[5].as_of.startswith(year)], paths)})
        positive_years = sum((row.get("avg_alpha_trade_return_pct") or 0.0) > 0 for row in yearly)
        results.append({
            "variant": variant,
            "participation_vs_baseline_pct": 0.0 if not baseline else round(100.0 * len(raw) / len(baseline), 2),
            "raw": _metric(raw, paths),
            "non_overlapping": _metric(independent, paths),
            "yearly": yearly,
            "positive_alpha_years": positive_years,
            "year_count": len(yearly),
            "worst_year_avg_alpha_pct": min((row["avg_alpha_trade_return_pct"] for row in yearly if row["avg_alpha_trade_return_pct"] is not None), default=None),
        })
    return {
        "method": "same_asof_multihorizon_confirmation_research_v1",
        "filter_contract": "filters use same-as-of champion directions only; future outcomes/MFE/MAE are evaluation-only",
        "execution_horizon_days": 5,
        "non_overlap_method": "per-symbol greedy 5D spacing after filtering",
        "drawdown_caveat": "event-level equal-weight diagnostic; overlapping cross-symbol windows are not capital constrained",
        "aligned_observations": len(aligned),
        "baseline_directional_observations": len(baseline),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-db", required=True)
    parser.add_argument("--codes", default="MSFT,GOOGL,QQQM,VOO")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    codes = [x.strip().upper() for x in args.codes.split(",") if x.strip()]
    series = load_sqlite_series(args.stock_db, codes=list(dict.fromkeys([*codes, "SPY", "QQQ"])))
    observations = replay_accuracy_lab(series, codes=codes, horizons=HORIZONS)
    payload = evaluate(observations, series)
    if payload["baseline_directional_observations"] <= 0:
        raise SystemExit("zero baseline directional observations")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in payload["results"]:
        m = item["non_overlapping"]
        print(item["variant"], json.dumps({"participation": item["participation_vs_baseline_pct"], **m, "positive_alpha_years": item["positive_alpha_years"], "year_count": item["year_count"], "worst_year_avg_alpha_pct": item["worst_year_avg_alpha_pct"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
