from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.legacy_archive import (
    export_verified_legacy_archive,
    inspect_legacy_facts,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicitly inspect or export a verified historical legacy V6 archive"
    )
    parser.add_argument(
        "--v6-db",
        default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Archive JSON path. A sidecar .manifest.json is created automatically.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional explicit manifest path.",
    )
    parser.add_argument(
        "--source-commit",
        default=os.getenv("GITHUB_SHA"),
        help="Source commit identity recorded in the archive manifest.",
    )
    parser.add_argument(
        "--engine-version",
        default=None,
        help="Optional expected legacy engine version; export fails if absent.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        result = inspect_legacy_facts(args.v6_db)
        result["mode"] = "dry_run"
        result["mutated"] = False
    else:
        output = args.output or (
            f"v6_reports/legacy_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        result = export_verified_legacy_archive(
            args.v6_db,
            output,
            manifest_path=args.manifest,
            source_commit=args.source_commit,
            engine_version=args.engine_version,
        )
        result["mode"] = "verified_export"
        result["source_mutated"] = False

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
