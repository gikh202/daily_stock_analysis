from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

try:
    from scripts.backtest_us_open_confirmation import build_observations, evaluate, load_candidates
    from scripts.run_us_open_confirmation import classify_confirmation
except ModuleNotFoundError:  # pragma: no cover
    from backtest_us_open_confirmation import build_observations, evaluate, load_candidates
    from run_us_open_confirmation import classify_confirmation


NY = ZoneInfo("America/New_York")

CONFIGS = {
    "baseline": dict(
        chase_tolerance_pct=0.5,
        weak_open_pct=-0.75,
        min_volume_ratio=0.55,
        min_opening_range_position=0.0,
    ),
    "range_filter_only": dict(
        chase_tolerance_pct=0.5,
        weak_open_pct=-0.75,
        min_volume_ratio=0.55,
        min_opening_range_position=0.25,
    ),
    "tighter_weakness_only": dict(
        chase_tolerance_pct=0.5,
        weak_open_pct=-0.5,
        min_volume_ratio=0.70,
        min_opening_range_position=0.0,
    ),
    "conservative_combined": dict(
        chase_tolerance_pct=0.5,
        weak_open_pct=-0.5,
        min_volume_ratio=0.70,
        min_opening_range_position=0.25,
    ),
    "very_conservative": dict(
        chase_tolerance_pct=0.25,
        weak_open_pct=-0.5,
        min_volume_ratio=0.70,
        min_opening_range_position=0.25,
    ),
}


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _entry_high(packet):
    execution = packet.get("execution") or {}
    entry = execution.get("entry_zone") or []
    if len(entry) != 2:
        return None
    return _finite(entry[1])


def evaluate_momentum_extension(rows):
    """Keep normal chase at 0.5%; allow 0.75% only on confirmed opening momentum."""
    buys = []
    status_counts = {}
    for observation in rows:
        decision = classify_confirmation(
            observation.packet,
            observation.snapshot,
            chase_tolerance_pct=0.5,
            weak_open_pct=-0.5,
            min_volume_ratio=0.70,
        )
        status = decision.status
        position = observation.opening_range_position

        if status == "BUY_NOW" and position is not None and position < 0.25:
            status = "WAIT_OPENING_RANGE"

        if status == "WAIT_PULLBACK":
            high = _entry_high(observation.packet)
            snapshot = observation.snapshot
            extension_ok = bool(
                high is not None
                and snapshot.current_price <= high * 1.0075
                and snapshot.return_from_open_pct > 0.0
                and position is not None
                and position >= 0.50
                and snapshot.volume_ratio is not None
                and snapshot.volume_ratio >= 0.70
            )
            if extension_ok:
                status = "BUY_NOW"

        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "BUY_NOW":
            buys.append(observation)

    close_returns = [item.close_return_pct for item in buys]
    hour_returns = [item.return_60m_pct for item in buys if item.return_60m_pct is not None]
    avg_close = mean(close_returns) if close_returns else None
    avg_mae = mean(item.mae_pct for item in buys) if buys else None
    return {
        "params": {
            "normal_chase_tolerance_pct": 0.5,
            "momentum_chase_extension_pct": 0.75,
            "weak_open_pct": -0.5,
            "min_volume_ratio_for_weak_price": 0.70,
            "min_opening_range_position": 0.25,
            "extension_min_opening_range_position": 0.50,
            "extension_requires_positive_from_open": True,
            "extension_min_volume_ratio": 0.70,
        },
        "observations": len(rows),
        "buy_count": len(buys),
        "buy_rate": len(buys) / len(rows) if rows else 0.0,
        "status_counts": status_counts,
        "win_rate_close": (
            sum(value > 0 for value in close_returns) / len(close_returns)
            if close_returns else None
        ),
        "avg_close_return_pct": avg_close,
        "median_close_return_pct": median(close_returns) if close_returns else None,
        "avg_60m_return_pct": mean(hour_returns) if hour_returns else None,
        "avg_mfe_pct": mean(item.mfe_pct for item in buys) if buys else None,
        "avg_mae_pct": avg_mae,
        "stop_hit_rate": sum(item.stop_hit for item in buys) / len(buys) if buys else None,
        "target1_hit_rate": sum(item.target1_hit for item in buys) / len(buys) if buys else None,
        "diagnostic_score": (
            avg_close - 0.35 * abs(avg_mae)
            if avg_close is not None and avg_mae is not None else None
        ),
        "buys": [
            {
                "symbol": item.symbol,
                "plan_date": item.plan_date,
                "session_date": item.session_date,
                "signal_price": item.snapshot.current_price,
                "return_from_open_pct": item.snapshot.return_from_open_pct,
                "volume_ratio": item.snapshot.volume_ratio,
                "opening_range_position": item.opening_range_position,
                "close_return_pct": item.close_return_pct,
                "return_60m_pct": item.return_60m_pct,
                "mfe_pct": item.mfe_pct,
                "mae_pct": item.mae_pct,
            }
            for item in buys
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare conservative US-open confirmation parameter sets")
    parser.add_argument("--v6-db", required=True)
    parser.add_argument("--output", default="open_confirmation_conservative_probe.json")
    args = parser.parse_args()

    observations = build_observations(load_candidates(args.v6_db))
    by_source = {}
    for observation in observations:
        by_source.setdefault(observation.source, []).append(observation)

    result = {
        "version": "us-open-conservative-probe-v2",
        "generated_at": datetime.now(NY).isoformat(),
        "sources": {},
    }
    for source, rows in sorted(by_source.items()):
        source_result = {}
        for name, params in CONFIGS.items():
            source_result[name] = evaluate(rows, **params)
        source_result["conditional_momentum_extension"] = evaluate_momentum_extension(rows)
        result["sources"][source] = source_result

    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
