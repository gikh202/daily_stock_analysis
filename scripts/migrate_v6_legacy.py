from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.legacy_archive import migrate_legacy_to_normalized


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicit legacy-to-normalized V6 migration; dry-run unless --apply is supplied"
    )
    parser.add_argument(
        "--v6-db",
        default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"),
    )
    parser.add_argument(
        "--engine-version",
        default=os.getenv("V6_ENGINE_VERSION") or V6DailyEngine().version,
    )
    parser.add_argument("--report-date", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = migrate_legacy_to_normalized(
        args.v6_db,
        engine_version=args.engine_version,
        apply=bool(args.apply),
        report_date=args.report_date,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
