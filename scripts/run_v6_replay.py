from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.replay import replay_stock_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict no-lookahead V6.1 historical replay from stock_daily")
    parser.add_argument("--stock-db", default="data/stock_analysis.db")
    parser.add_argument("--codes", default="", help="Comma-separated symbols; blank replays all non-benchmark symbols")
    parser.add_argument("--output", default="v6_reports/v6_replay.json")
    args = parser.parse_args()

    codes = [value.strip().upper() for value in args.codes.split(",") if value.strip()]
    result = replay_stock_db(args.stock_db, codes=codes or None)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
