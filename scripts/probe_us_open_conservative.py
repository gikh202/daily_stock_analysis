from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts.backtest_us_open_confirmation import build_observations, evaluate, load_candidates
except ModuleNotFoundError:  # pragma: no cover
    from backtest_us_open_confirmation import build_observations, evaluate, load_candidates


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
        "version": "us-open-conservative-probe-v1",
        "generated_at": datetime.now(NY).isoformat(),
        "sources": {},
    }
    for source, rows in sorted(by_source.items()):
        source_result = {}
        for name, params in CONFIGS.items():
            source_result[name] = evaluate(rows, **params)
        result["sources"][source] = source_result

    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
