from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.research_backfill import (
    DEFAULT_HISTORY_YEARS,
    DEFAULT_MINIMUM_BARS,
    backfill_accuracy_research_history,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clone production stock history and backfill an isolated V6 accuracy research DB"
    )
    parser.add_argument("--source-db", default="data/stock_analysis.db")
    parser.add_argument("--research-db", default="v6_research/stock_analysis_research.db")
    parser.add_argument("--codes", default="")
    parser.add_argument("--history-years", type=int, default=DEFAULT_HISTORY_YEARS)
    parser.add_argument("--minimum-bars", type=int, default=DEFAULT_MINIMUM_BARS)
    parser.add_argument(
        "--summary",
        default="v6_reports/accuracy_weekly/v6_accuracy_history_backfill.json",
    )
    args = parser.parse_args()

    codes = [value.strip().upper() for value in args.codes.split(",") if value.strip()]
    payload = backfill_accuracy_research_history(
        args.source_db,
        args.research_db,
        codes=codes or None,
        history_years=max(1, int(args.history_years)),
        minimum_bars=max(1, int(args.minimum_bars)),
    )

    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    compact = {
        "status": payload.get("status"),
        "source_unchanged": payload.get("source_unchanged"),
        "research_rows": payload.get("research_rows"),
        "eligible_targets": payload.get("eligible_targets"),
        "benchmark_ready": payload.get("benchmark_ready"),
        "errors": payload.get("errors"),
        "summary": str(summary_path),
    }
    print(json.dumps(compact, ensure_ascii=False, sort_keys=True))

    if not payload.get("source_unchanged"):
        return 2
    if int(payload.get("eligible_target_count") or 0) <= 0:
        return 3
    if not all(bool(value) for value in (payload.get("benchmark_ready") or {}).values()):
        return 4
    if str(payload.get("research_quick_check") or "").lower() != "ok":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
